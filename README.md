# Snow White
Quiet and resume Sidekiq workers in AWS Elastic Beanstalk environments

![Snow White](https://github.com/rewindio/snowwhite/blob/master/images/snowwhite-emoji.png?raw=true)

# Prerequsites
* The AWS CLI installed and configured with profiles called staging and production
* Your AWS IAM user has access to create tasks on AWS Fargate
* (optional) Your Slack User ID has been associated with your IAM user in AWS

# Slack Installation
Within Slack, create a custom emoji called `snowwhite` using the emoji in the `images` folder.  The Snow White emoji is free use and courtesy of [ClipArtMax](https://www.clipartmax.com)

Create an [incoming Webhook](https://api.slack.com/incoming-webhooks) according to the Slack documentation and obtain the URL.

We are using the legacy Slack webhook to post to Slack to channels.  This can also DM users via Slackbot but only if the UID is known.  The UID needs to be added to a tag called `slack_userid` on any IAM users that will invoke Snow White.

To obtain the Slack userID, visit Slack in a browser, view a user's profile and hover on `Direct Messages`.  In the status bar of the browser, you will see the UID of the form Uxxxxxxx.  It MUST begin with U.  Grab this ID and add it to a tag on the user in AWS IAM

# Elastic Beanstalk Configuration
Instances that you wish to use Snow White with must be running under a role that allows the SSM agent to run.  Generally, just attach the managed policy `AmazonEC2RoleForSSM` to the role to grant the required permissions.
Snow White was tested on Elasticbeanstalk v3 which uses EC2 instances running amazon linux 2, systemd and a single sidekiq service.  Note that original implementation used Elasticbeanstalk v2, upstart and 2 sidekiq processes.

# AWS Installation
The pieces which run in AWS are managed via Cloudformation.  Create a stack using the template in the `cfn` folder.  It takes 4 parameters:

* CfnStackName - the name of the stack you are creating in each region (recommend: snow-white)
* CfnWakeDocName - the logical name of the wake command (leave as default)
* QuietCommnadName - the logical name of the quiet command (leave as default)
* SlackWebhook - the full URL of a Slack incoming webhook 
* SlackChannel - the name of the Slack channel to send notifications to

You may also build and deploy using AWS SAM (tested with SAM CLI, version 1.22.0):

## Build

```shell
sam build --use-container --debug --template cfn/snow-white-cfn.yml
```

## Deploy

```shell
sam deploy --debug --stack-name snow-white \
  --s3-bucket deploy-bucket \
  --region us-east-1 \
  --template cfn/snow-white-cfn.yml \
  --profile staging \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides "SlackChannel=#snow_white_messages \
                        SlackWebhook=https://hooks.slack.com/services/ABC/123 \
                        CfnQuietDocName=QuietSideKiqDocument \
                        CfnWakeDocName=WakeSideKiqDocument \
                        CfnStopDocName=StopSideKiqDocument \
                        CfnStackName=snow-white"
```

# Helper script Configuration (snow-white.sh)
A small helper script is provided (syntax below) to invoke the Snow White fargate task.  Before running for the first time, you will need to set a few variables at the top of the script:

* ECS_CLUSTER_REGION - the region that contains your ECS cluster (this does not have to be the same as the region your Sidekiq workers are in)
* ECS_CLUSTER_NAME - name of a pre-existing cluster to spin up Fargate tasks in
* vpc_id - the ID of a VPC to create Fargate tasks in
* subnet_id - the ID of a subnet in the VPC to create tasks in
* sec_group - the ID of a security group to attach to the Fadget tasks (this does not need to allow any inbound ports)

# Usage
Clone this repo and run the snow-white.sh helper script.

`usage: usage: snow-white -a quiet|wake|stop -e <EB env name pattern> -f staging|production  -p <EB app name> -r <region>`

Where:
* -a designates if we are quieting or waking the workers
* -e is a filter for beanstalk environment names to match that are running Sidekiq workers (ie. workers)
* -p is the Elastic beanstalk application name
* -r is the AWS region (ie. us-east-1)
* -f is the AWS profile to use

Example:
`./snow-white.sh -a quiet -e workers -p MY-EB-APP -r us-east-1 -f staging`

This will submit a task to AWS Fargate which will in turn run an AWS SSM command on each instance of all Sidekiq workers in the EB application whose environment names contain the string `workers`.  The Fargate task will then monitor each worker until the queue is drained and will report back via Slack

# Moving Parts
Snow White consists of a few moving parts.

## SSM Run Commands
These are contained in the `ssm` folder of this repo.  These are the parts that do the real work to quiet or wake the Sidekiq workers and are referenced in AWS SSM documents in the Cloudformation template.  SSM will pull the scripts from this repo at execution time using the `aws:downloadContent` action in the SSM document.

## Fargate container
We need a long running task to actually invoke the SSM command and notify us when it is complete.  Ideally, this would be a Lambda but as Sidekiq workers can be busy for hours after reciving the quiet signal, the best solution is a Docket container run on demand in Fargate.  This container finds the EC2 instances that are part of the Elastic Beanstalk environments and then invokes the SSM command to quiet or wake the Sidekiq workers on them.  It will poll under the command reports complete and then notify via Slack with the final status.
