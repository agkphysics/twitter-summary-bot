import boto3

ssm = boto3.client("ssm")

res = ssm.get_parameters_by_path(Path="/gpt-bot", WithDecryption=True)
params = {p["Name"].split("/")[-1]: p["Value"] for p in res["Parameters"]}

CONSUMER_KEY = params["CONSUMER_KEY"]
CONSUMER_SECRET = params["CONSUMER_SECRET"]
OPENAI_API_KEY = params["OPENAI_API_KEY"]
APP_BEARER_TOKEN = params["APP_BEARER_TOKEN"]
OAUTH1_BOT_ACCESS_TOKEN = params["OAUTH1_BOT_ACCESS_TOKEN"]
OAUTH1_BOT_TOKEN_SECRET = params["OAUTH1_BOT_TOKEN_SECRET"]
