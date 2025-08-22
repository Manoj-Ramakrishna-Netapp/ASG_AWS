import boto3
import json

def main():
    # Dynamically retrieve the list of available regions
    ec2 = boto3.client('ec2')
    regions_response = ec2.describe_regions()
    regions = [region['RegionName'] for region in regions_response['Regions']]
    #regions = ['us-west-2']  # For testing, you can specify a single region
    checked_regions = []
    regions_with_no_encryption = set()
    updated_asgs = []
    errors = []

    def list_auto_scaling_groups(autoscaling_client):
        try:
            paginator = autoscaling_client.get_paginator('describe_auto_scaling_groups')
            page_iterator = paginator.paginate()

            all_auto_scaling_groups = []
            for page in page_iterator:
                all_auto_scaling_groups.extend(page['AutoScalingGroups'])
            return all_auto_scaling_groups
        except Exception as e:
            errors.append(f"Error listing ASGs: {str(e)}")
            return []

    def is_encrypted(ec2_client, launch_template):
        try:
            response = ec2_client.describe_launch_template_versions(
                LaunchTemplateName=launch_template.get('LaunchTemplateName'),
                Versions=[launch_template.get('Version')]
            )
            for version in response['LaunchTemplateVersions']:
                for block_device in version['LaunchTemplateData']['BlockDeviceMappings']:
                    if 'Ebs' not in block_device:
                        errors.append(f"Code 404: EBS device not found in launch template {launch_template.get('LaunchTemplateName')}")
                        return False
                    if not block_device['Ebs'].get('Encrypted', False):
                        return False
            return True
        except Exception as e:
            errors.append(f"Code 505x: Failed to check encryption for {launch_template.get('LaunchTemplateName')}: {str(e)}")
            return True  # Assume encrypted if there's an error to avoid false positives

    def update_launch_template(ec2_client, launch_template, region):
        try:
            response = ec2_client.describe_launch_template_versions(
                LaunchTemplateName=launch_template.get('LaunchTemplateName'),
                Versions=[launch_template.get('Version')]
            )
            current_version = response['LaunchTemplateVersions'][0]['LaunchTemplateData']
            updated_block_devices = []

            for block_device in current_version['BlockDeviceMappings']:
                if 'Ebs' not in block_device:
                    errors.append(f"Code 504x: Device type {block_device['DeviceName']} is not coded to enable encryption. Please contact Sys Arch.")
                    continue
                if not block_device['Ebs'].get('Encrypted', False):
                    try:
                        block_device['Ebs']['Encrypted'] = True
                        updated_block_devices.append(block_device['DeviceName'])
                    except Exception as e:
                        errors.append(f"Code 505x: Failed to enable encryption for device {block_device['DeviceName']} in {launch_template.get('LaunchTemplateName')}: {str(e)}")

            response = ec2_client.create_launch_template_version(
                LaunchTemplateName=launch_template.get('LaunchTemplateName'),
                SourceVersion=launch_template.get('Version'),
                LaunchTemplateData=current_version
            )
            new_version = response['LaunchTemplateVersion']['VersionNumber']

            ec2_client.modify_launch_template(
                LaunchTemplateName=launch_template.get('LaunchTemplateName'),
                DefaultVersion=str(new_version)
            )
            updated_asgs.append((region, launch_template.get('LaunchTemplateName'), updated_block_devices))
        except Exception as e:
            errors.append(f"Error updating launch template {launch_template.get('LaunchTemplateName')}: {str(e)}")

    for region in regions:
        checked_regions.append(region)
        autoscaling = boto3.client('autoscaling', region_name=region)
        ec2 = boto3.client('ec2', region_name=region)

        auto_scaling_groups = list_auto_scaling_groups(autoscaling)

        for asg in auto_scaling_groups:
            try:
                launch_template = asg.get('LaunchTemplate')
                if launch_template:
                    if not is_encrypted(ec2, launch_template):
                        regions_with_no_encryption.add(region)
                        update_launch_template(ec2, launch_template, region)
            except Exception as e:
                errors.append(f"Error processing ASG {asg.get('AutoScalingGroupName')}: {str(e)}")

    # Prepare the response
    if errors:
        status_code = 500
        message = f"Errors occurred: {errors}"
    else:
        status_code = 200
        message = (
            f"Regions Checked: {checked_regions}\n"
            f"Regions with NO encryption: {list(regions_with_no_encryption)}\n"
            f"Updated regions and ASGs with block devices: {updated_asgs}"
        )

    # Print the message directly for readability
    print({
        'statusCode': status_code,
        'body': message
    })

if __name__ == "__main__":
    main()
