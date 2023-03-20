# Twitter summary bot

This is a Twitter bot that summarises Tweet threads into a single Tweet
using OpenAI's GPT-based models. The bot Twitter account is
[@GPTSummary](https://twitter.com/GPTSummary). The bot is hosted on AWS
using AWS Lambda functions to receive and process webhooks.

## Installation
Install the [AWS SAM
CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html). Once installed, you can build the Python bot:
```
sam build --config-env python
```
Once built, you can deploy to AWS:
```
sam deploy
```
This will create an AWS Cloudformation stack for the bot. It will then
create an API Gateway HTTP API, Lambda function, and needed IAM roles.

## Keys
The code obtains API keys from the [AWS Systems Manager Parameter
Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html)
at runtime. You should create parameters with the prefix `/gpt-bot/`
(e.g. `/gpt-bot/CONSUMER_KEY`).
