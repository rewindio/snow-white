import json
import os
import pprint
import sys
import time

import boto3
import certifi
import urllib3
from botocore.exceptions import ClientError


instances_for_command = {}
instances_command_status = {}
ssm_failed_statuses = ["Cancelled", "TimedOut", "Failed"]
failed_commands = False
slack_string = ""

#
# Queries the ECS task metadata service and returns the resulting json blob
#
def get_task_metadata(http_client_pool):
    ecs_metadata_url = "169.254.170.2/v2/metadata"
    response = http_client_pool.request(
        "GET", ecs_metadata_url, headers={"Content-Type": "text/html"}
    )

    if response.status == 200:
        return response.data
    else:
        return "{}"


def get_invoking_user(http_client_pool, ecs_client):
    response = None
    started_by = None
    invoking_user = {}

    task_metadata = json.loads(get_task_metadata(http_client_pool))

    ecs_cluster = task_metadata["Cluster"]
    task_arn = task_metadata["TaskARN"]

    print("Describing task " + task_arn + " in cluster " + ecs_cluster)

    # Even though we are in the task, this information is not in the metadata for "us"
    # So we need to query our own task
    try:
        response = ecs_client.describe_tasks(cluster=ecs_cluster, tasks=[task_arn])
    except ClientError as e:
        print("Unexpected error describing ECS task: " + e.response["Error"]["Code"])

    if response:
        if len(response["tasks"]) > 0:
            # we may not have a started_by field depending on how the task was started
            if "startedBy" in response["tasks"][0]:
                started_by = response["tasks"][0]["startedBy"]

                invoking_user["user"] = started_by

    return invoking_user


#
# Post a message to a Slack channel or direct to a user using the incoming webhook URL
#
def post_to_slack_channel(http_client_pool, webhook_url, channel, message):
    status = False

    encoded_data = json.dumps(
        {
            "username": "Snow White",
            "icon_emoji": ":snowwhite:",
            "channel": channel,
            "text": message,
        }
    ).encode("utf-8")

    response = http_client_pool.request(
        "POST",
        webhook_url,
        body=encoded_data,
        headers={"Content-Type": "application/json"},
    )

    if response.status == 200:
        status = True
    else:
        status = False

    return status


#
# Gets a list of all the sidekiq worker EB envs and their IDs
#
def get_eb_worker_envs(eb_app_name, eb_env_name_pattern_string, eb_client):
    eb_envs = {}

    response = eb_client.describe_environments(
        ApplicationName=eb_app_name, IncludeDeleted=False
    )

    for eb_env in response["Environments"]:
        eb_env_name = eb_env["EnvironmentName"]
        eb_env_id = eb_env["EnvironmentId"]

        if eb_env_name_pattern_string in eb_env_name.lower():
            eb_envs[eb_env_id] = eb_env_name

    return eb_envs


#
# Get the instances in the EB environment
# Append to the passed-in dict
#
def get_eb_instances(envid, instances_for_command, eb_client):

    response = eb_client.describe_environment_resources(EnvironmentId=envid)

    # We save the environment name with the instance ID in case the command fails
    # and we need to report back which environment may not be fully paused...
    for instance in response["EnvironmentResources"]["Instances"]:
        instances_for_command[instance["Id"]] = response["EnvironmentResources"][
            "EnvironmentName"
        ]


#
# Get the name of the SSM command document to run from the CFN stack resources
#
def get_ssm_doc_name(cfn_stack_name, logical_doc_name, cfn_client):

    ssm_doc_name = ""
    response = None

    try:
        response = cfn_client.describe_stack_resource(
            StackName=cfn_stack_name, LogicalResourceId=logical_doc_name
        )

    except ClientError as e:
        print("Unexpected error obtaining SSM document: " + e.response["Error"]["Code"])

    if response:
        if "PhysicalResourceId" in response["StackResourceDetail"]:
            ssm_doc_name = response["StackResourceDetail"]["PhysicalResourceId"]

    return ssm_doc_name


#
# Submit the run command to SSM to run on our list of instances
#
def submit_ssm_command(instances_for_command, document_name, ssm_client):

    instance_id_list = []
    command_id = None

    for instance in instances_for_command:
        instance_id_list.append(instance)

    print("submit_ssm_command: instance list")
    pprint.pprint(instance_id_list)

    if len(instance_id_list) > 0:
        print("submit_ssm_command: doc:" + document_name)

        try:
            response = ssm_client.send_command(
                InstanceIds=instance_id_list,
                DocumentName=document_name,
                DocumentVersion="$LATEST",
            )

            command_id = response["Command"]["CommandId"]
        except ClientError as e:
            print(
                "Unexpected error when submitting SSM command: "
                + e.response["Error"]["Code"]
            )

    return command_id


#
# Mainline
#
if "WORKER_ACTION" not in os.environ:
    print("Error: WORKER_ACTION environment variable must be set")
    sys.exit(-1)
else:
    worker_action = os.environ["WORKER_ACTION"].lower()

if "EB_APP_NAME" not in os.environ:
    print("Error: EB_APP_NAME environment variable must be set")
    sys.exit(-1)
else:
    eb_app_name = os.environ["EB_APP_NAME"]

if "AWS_REGION" not in os.environ:
    print("No AWS_REGION environment variable found - assuming us-east-1")
    aws_region = "us-east-1"
else:
    aws_region = os.environ["AWS_REGION"]

if "ECS_CLUSTER_REGION" not in os.environ:
    print("No ECS_CLUSTER_REGION environment variable found - assuming us-east-1")
    ecs_cluster_region = "us-east-1"
else:
    ecs_cluster_region = os.environ["ECS_CLUSTER_REGION"]

if "QUIET_COMMAND_LOGICAL_NAME" not in os.environ:
    print("Error: QUIET_COMMAND_LOGICAL_NAME environment variable must be set")
    sys.exit(-1)
else:
    quiet_command_logical_name = os.environ["QUIET_COMMAND_LOGICAL_NAME"]

if "WAKE_COMMAND_LOGICAL_NAME" not in os.environ:
    print("Error: WAKE_COMMAND_LOGICAL_NAME environment variable must be set")
    sys.exit(-1)
else:
    wake_command_logical_name = os.environ["WAKE_COMMAND_LOGICAL_NAME"]

if "STOP_COMMAND_LOGICAL_NAME" not in os.environ:
    print("Error: STOP_COMMAND_LOGICAL_NAME environment variable must be set")
    sys.exit(-1)
else:
    stop_command_logical_name = os.environ["STOP_COMMAND_LOGICAL_NAME"]

if "CFN_STACK_NAME" not in os.environ:
    print("Error: CFN_STACK_NAME environment variable must be set")
    sys.exit(-1)
else:
    cfn_stack_name = os.environ["CFN_STACK_NAME"]

if "SLACK_WEBHOOK" in os.environ:
    slack_webhook_url = os.environ["SLACK_WEBHOOK"]

if "NOTIFY_SLACK_CHANNEL" in os.environ:
    notify_slack_channel = os.environ["NOTIFY_SLACK_CHANNEL"]

if "EB_ENV_NAME_PATTERN_STRING" not in os.environ:
    print(
        "No EB_ENV_NAME_PATTERN_STRING environment variable found - using workers as the environment pattern"
    )
    eb_env_name_pattern_string = "workers"
else:
    eb_env_name_pattern_string = os.environ["EB_ENV_NAME_PATTERN_STRING"]


# Http client Pool
http = urllib3.PoolManager(cert_reqs="CERT_REQUIRED", ca_certs=certifi.where())

# Boto clients
eb_client = boto3.client("elasticbeanstalk", region_name=aws_region)
ssm_client = boto3.client("ssm", region_name=aws_region)
cfn_client = boto3.client("cloudformation", region_name=aws_region)

ecs_client = boto3.client("ecs", region_name=ecs_cluster_region)


# Who started this task?
invoking_user = get_invoking_user(http, ecs_client)

# Get a list of all the sidekiq worker envs in the EB application
sidekiq_worker_eb_envs = get_eb_worker_envs(
    eb_app_name, eb_env_name_pattern_string, eb_client
)

if len(sidekiq_worker_eb_envs) == 0:
    # No worker environments found
    post_to_slack_channel(http, slack_webhook_url, notify_slack_channel, slack_string)
else:
    # We have some workers to work with

    if "user" in invoking_user:
        slack_string = (
            "*_"
            + invoking_user["user"]
            + "_* has asked me to _"
            + worker_action
            + "_ the Sidekiq workers for all environment names containing _"
            + eb_env_name_pattern_string
            + "_ in *"
            + eb_app_name
            + " ("
            + aws_region
            + ").* I'll let you know when they are done"
        )
        post_to_slack_channel(
            http, slack_webhook_url, notify_slack_channel, slack_string
        )
    else:
        slack_string = (
            "Someone has asked me to _"
            + worker_action
            + "_ the Sidekiq workers for all environment names containing _"
            + eb_env_name_pattern_string
            + "_ in *"
            + eb_app_name
            + " ("
            + aws_region
            + ").* I'll let you know when they are done"
        )
        post_to_slack_channel(
            http, slack_webhook_url, notify_slack_channel, slack_string
        )

    # get the EC2 instances for each worker env
    for worker in sidekiq_worker_eb_envs:
        get_eb_instances(worker, instances_for_command, eb_client)

    # Run our command on the list of instances for all environments
    if worker_action == "quiet":
        print("Running the QUIET command on the workers")
        quiet_command_doc = get_ssm_doc_name(
            cfn_stack_name, quiet_command_logical_name, cfn_client
        )

        if quiet_command_doc:
            command_id = submit_ssm_command(
                instances_for_command, quiet_command_doc, ssm_client
            )
        else:
            print("Error occurred getting the quiet command doc name")
    elif worker_action == "stop":
        print("Running the STOP command on the workers")
        stop_command_doc = get_ssm_doc_name(
            cfn_stack_name, stop_command_logical_name, cfn_client
        )

        if stop_command_doc:
            command_id = submit_ssm_command(
                instances_for_command, stop_command_doc, ssm_client
            )
        else:
            print("Error occurred getting the stop command doc name")
    elif worker_action == "wake":
        print("Running the WAKE command on the workers")

        wake_command_doc = get_ssm_doc_name(
            cfn_stack_name, wake_command_logical_name, cfn_client
        )

        if wake_command_doc:
            command_id = submit_ssm_command(
                instances_for_command, wake_command_doc, ssm_client
            )
        else:
            print("Error occurred getting the wake command doc name")

    time.sleep(
        2
    )  # https://stackoverflow.com/questions/50067035/retrieving-command-invocation-in-aws-ssm

    if command_id:
        print("The SSM command ID is " + command_id)

        # To get the command status, we have to check each instance individually
        max_attempts = 360
        attempts = 1
        while attempts <= max_attempts:
            for instance in instances_for_command:
                command_status_string = None
                print(
                    "Checking command status for "
                    + instance
                    + " in command "
                    + command_id
                )
                try:
                    command_status = ssm_client.list_command_invocations(
                        CommandId=command_id, InstanceId=instance, Details=True
                    )

                    # Do we have a status?
                    if "CommandInvocations" in command_status:
                        if len(command_status["CommandInvocations"]) > 0:
                            if "Status" in command_status["CommandInvocations"][0]:
                                command_status_string = command_status[
                                    "CommandInvocations"
                                ][0]["Status"]
                            else:
                                print("No status found in command_status")
                                pprint.pprint(command_status)
                        else:
                            print("No invocations found in command_status")
                            pprint.pprint(command_status)
                    else:
                        print("No command invocations found in command_status")
                        pprint.pprint(command_status)

                    if command_status_string:
                        print("The command status is " + command_status_string)

                        # Check if the command worked
                        if command_status_string == "Success":
                            instances_command_status[instance] = "SUCCESS"
                        elif command_status_string in ssm_failed_statuses:
                            status_code = command_status["ResponseCode"]
                            instances_command_status[instance] = "FAILED-" + str(
                                status_code
                            )
                            failed_commands = True

                except ClientError as e:
                    print(
                        "Unexpected error checking command status: "
                        + e.response["Error"]["Code"]
                    )
                    # Do not break here, we will retry next time around

            # Short circuit the loop if we have a valid status for each instance
            if len(instances_for_command) == len(instances_command_status):
                break

            max_attempts += 1
            time.sleep(10)
    else:
        print("ERROR: No command id returned from the ssm command send")
        sys.exit(-2)

    if failed_commands:
        # We had at least some commands that failed to run
        slack_string = (
            "Uh oh.  I had a problem telling the workers to "
            + worker_action
            + " on these instances in *"
            + eb_app_name
            + " ("
            + aws_region
            + ")*\n\n"
        )

        for instance in instances_command_status:
            if "FAILED" in instances_command_status[instance]:
                msg, code = instances_command_status[instance].split("-")

                if code == 1:
                    slack_string += (
                        instance
                        + " ("
                        + instances_for_command[instance]
                        + ") - worker not quieted\n"
                    )
                elif code == 2:
                    slack_string += (
                        instance
                        + " ("
                        + instances_for_command[instance]
                        + ") - worker still running jobs\n"
                    )
                else:
                    slack_string += (
                        instance + " (" + instances_for_command[instance] + ")\n"
                    )
    else:
        # All good, output great success
        if worker_action == "quiet":
            slack_string = (
                "The workers are all quiet for all environment names containing _"
                + eb_env_name_pattern_string
                + "_ in *"
                + eb_app_name
                + " ("
                + aws_region
                + ")*"
            )
        elif worker_action == "wake":
            slack_string = (
                "The workers are all awake for all environment names containing _"
                + eb_env_name_pattern_string
                + "_ in *"
                + eb_app_name
                + " ("
                + aws_region
                + ")*"
            )
        elif worker_action == "stop":
            slack_string = (
                "The workers are all stopped for all environment names containing _"
                + eb_env_name_pattern_string
                + "_ in *"
                + eb_app_name
                + " ("
                + aws_region
                + ")*"
            )

    post_to_slack_channel(http, slack_webhook_url, notify_slack_channel, slack_string)
