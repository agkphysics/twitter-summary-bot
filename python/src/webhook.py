import base64
import hmac
import os
from typing import Any

from aws_lambda_powertools.event_handler.api_gateway import APIGatewayHttpResolver
from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
    UnauthorizedError,
)
from aws_lambda_powertools.logging import Logger, correlation_paths

from keys import CONSUMER_SECRET
from tweets import get_conversation_summary, reply_to_user

app = APIGatewayHttpResolver()
logger = Logger(service="twitter-webhook", level=os.environ.get("LOG_LEVEL", "INFO"))

BOT_USER_ID = os.environ["BOT_USER_ID"]


@app.get("/webhooks/twitter")
def webhook_challenge():
    # "Header names are lowercased" - AWS docs
    if "x-twitter-webhooks-signature" not in app.current_event.headers:
        raise UnauthorizedError("Invalid signature")
    twitter_sig = app.current_event.headers["x-twitter-webhooks-signature"]
    if not twitter_sig.startswith("sha256="):
        raise UnauthorizedError("Invalid signature")
    if not hmac.compare_digest(
        hmac.digest(
            CONSUMER_SECRET.encode("utf-8"),
            app.current_event.raw_query_string.encode("utf-8"),
            "sha256",
        ),
        base64.b64decode(twitter_sig[7:]),
    ):
        raise UnauthorizedError("Invalid signature")

    if "crc_token" not in app.current_event.query_string_parameters:
        raise BadRequestError("crc_token not found")
    crc_token = app.current_event.query_string_parameters["crc_token"]

    # creates HMAC SHA-256 hash from incomming token and your consumer secret
    hash = hmac.digest(
        CONSUMER_SECRET.encode("utf-8"), crc_token.encode("utf-8"), "sha256"
    )

    # construct response data with base64 encoded hash
    response = {"response_token": "sha256=" + base64.b64encode(hash).decode("utf-8")}
    return response


@app.post("/webhooks/twitter")
def webhook_data() -> dict[str, Any]:
    data: dict[str, Any] = app.current_event.json_body
    if "tweet_create_events" not in data:
        logger.info("No tweet_create_events in data")
        return {}
    for tweet in data["tweet_create_events"]:
        tweet_id = int(tweet["id_str"])
        for mention in tweet["entities"]["user_mentions"]:
            if mention["id_str"] == BOT_USER_ID:
                summary = get_conversation_summary(tweet_id)
                if summary is None:
                    logger.error("Could not get summary for tweet %s", tweet_id)
                else:
                    reply_to_user(tweet_id, summary)
                break
        else:
            logger.info("No mention of bot in tweet")
    return {}


@logger.inject_lambda_context(
    correlation_id_path=correlation_paths.API_GATEWAY_HTTP, log_event=True
)
def lambda_handler(event, context):
    return app.resolve(event, context)
