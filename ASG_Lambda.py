import boto3
import json

def lambda_handler(event, context):
    # Dynamically retrieve the list of available regions
    ec2 = boto3.client('ec2')
    regions_response = ec2.describe_regions()
    regions = [region['RegionName'] for region in regions_response['Regions']]

    checked_regions = []
    regions_with_no_encryption = set()
    updated_asgs = []
    errors = []

    def list_auto_scaling_groups(autoscaling_client):
        try:
            response = autoscaling_client.describe_auto_scaling_groups()
            return response['AutoScalingGroups']
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
                    if not block_device['Ebs'].get('Encrypted', False):
                        return False
            return True
        except Exception as e:
            errors.append(f"Error checking encryption for {launch_template.get('LaunchTemplateName')}: {str(e)}")
            return True  # Assume encrypted if there's an error to avoid false positives

    def update_launch_template(ec2_client, launch_template, region):
        try:
            # Retrieve the current launch template version details
            response = ec2_client.describe_launch_template_versions(
                LaunchTemplateName=launch_template.get('LaunchTemplateName'),
                Versions=[launch_template.get('Version')]
            )
            current_version = response['LaunchTemplateVersions'][0]['LaunchTemplateData']

            # Track block devices that are updated
            updated_block_devices = []

            # Update all block device mappings to have encryption enabled
            for block_device in current_version['BlockDeviceMappings']:
                if not block_device['Ebs'].get('Encrypted', False):
                    block_device['Ebs']['Encrypted'] = True
                    updated_block_devices.append(block_device['DeviceName'])

            # Create a new launch template version with updated encryption settings
            response = ec2_client.create_launch_template_version(
                LaunchTemplateName=launch_template.get('LaunchTemplateName'),
                SourceVersion=launch_template.get('Version'),
                LaunchTemplateData=current_version
            )
            new_version = response['LaunchTemplateVersion']['VersionNumber']

            # Set the new version as the default
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

    return {
        'statusCode': status_code,
        'body': json.dumps(message)
    }