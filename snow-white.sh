#!/bin/bash 

ECS_CLUSTER_REGION=us-east-1
ECS_CLUSTER_NAME=YOUR ECS CLUSTER

vpc_id=YOUR VPC ID
subnet_id=YOUR SUBNET ID
sec_group=YOUR SECURITY GROUP ID


show_help() {
    echo "usage: snow-white -a quiet|wake -e <EB env name pattern> -f staging|production  -p <EB app name> -r <region>"
}

#
# Main Line
#
OPTIND=1
while getopts "hp:a:e:r:f:" opt; do
    case "$opt" in
    h)
        show_help
        exit 0
        ;;
    e)  ebenvpattern=$OPTARG
        ;;
    f)  profile=$OPTARG
        ;;
    p)  ebapp=$OPTARG
        ;;
    r)  region=$OPTARG
        ;;
    a)  action=$OPTARG
        ;;
    esac
done
shift $((OPTIND-1))

if [ -z "${profile}" ] || [ -z "${ebapp}" ] || [ -z "${action}" ] || [ -z "${region}" ] || [ -z "${ebenvpattern}" ]; then
    show_help
    exit 1
else
    if [ "${action}" != "quiet" ] && [ "${action}" != "wake" ]; then
        show_help
        exit 1
    fi

    if [ "${profile}" != "staging" ] && [ "${profile}" != "production" ]; then
        show_help
        exit 1
    fi

fi

# Write the basic parms for the task
cat << EOF > /tmp/snow-white_overrides.$$
{
  "containerOverrides": [
    {
      "name": "snow-white",
      "environment": [
        {
          "name": "ECS_CLUSTER_REGION",
          "value": "${ECS_CLUSTER_REGION}"
        },
        {
          "name": "WORKER_ACTION",
          "value": "${action}"
        },
          {
          "name": "EB_APP_NAME",
          "value": "${ebapp}"
        },
        {
          "name": "EB_ENV_NAME_PATTERN_STRING",
          "value": "${ebenvpattern}"
        },
        {
          "name": "AWS_REGION",
          "value": "${region}"
        }
      ]
    }
  ]
}
EOF

cat << EOF > /tmp/snow-white_network.$$
{
  "awsvpcConfiguration": {
    "subnets": ["${subnet_id}"],
    "securityGroups": ["${sec_group}"],
    "assignPublicIp": "ENABLED"
  }
}
EOF

current_aws_user=$(aws sts get-caller-identity --query 'Arn' --output text --region ${ECS_CLUSTER_REGION} |cut -f 2 -d /)

if [ -z "${current_aws_user}" ]; then
    current_aws_user=Unknown
fi

ecs_task=$(aws ecs run-task \
            --overrides  file:///tmp/snow-white_overrides.$$\
            --cluster ${ECS_CLUSTER_NAME} \
            --task-definition snow-white \
            --launch-type "FARGATE" \
            --network-configuration file:///tmp/snow-white_network.$$\
            --started-by ${current_aws_user} \
            --profile ${profile} \
            --region ${ECS_CLUSTER_REGION}
            )

if [[ ${ecs_task} == *"PROVISIONING"* ]]; then
    echo "Submitted task to Fargate - Snow White will send you updates in Slack"
else
    echo "Something went wrong submitting a task to fargate"
    echo ${ecs_task}
fi