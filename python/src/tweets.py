import datetime
import time
from typing import Optional

import openai
import tweepy
from aws_lambda_powertools.logging import Logger

from keys import (
    APP_BEARER_TOKEN,
    CONSUMER_KEY,
    CONSUMER_SECRET,
    OAUTH1_BOT_ACCESS_TOKEN,
    OAUTH1_BOT_TOKEN_SECRET,
    OPENAI_API_KEY,
)

openai.api_key = OPENAI_API_KEY

logger = Logger(service="twitter-webhook", child=True)

tw_client = tweepy.Client(
    bearer_token=APP_BEARER_TOKEN,
    consumer_key=CONSUMER_KEY,
    consumer_secret=CONSUMER_SECRET,
    access_token=OAUTH1_BOT_ACCESS_TOKEN,
    access_token_secret=OAUTH1_BOT_TOKEN_SECRET,
)


def get_conversation_id(tweet_id: str) -> Optional[str]:
    """Get the conversation ID of a tweet.

    Args
    ----
    tweet_id: str
        The ID of the tweet to get the conversation ID of.

    Returns
    -------
    Optional[str]
        The conversation ID of the tweet, or None if the tweet doesn't exist.
    """

    resp = tw_client.get_tweet(
        tweet_id,
        tweet_fields=["conversation_id", "author_id", "referenced_tweets"],
        expansions=["referenced_tweets.id"],
    )
    tweet: tweepy.Tweet = resp.data
    logger.debug("Tagging tweet: %s", tweet)

    if resp.errors:
        for error in resp.errors:
            logger.error("[ERROR] %s: %s", error["title"], error["detail"])
        return None

    if tweet.referenced_tweets:
        for ref in tweet.referenced_tweets:
            if ref.type == "quoted":  # Tagging tweet is a quote tweet
                qt: tweepy.Tweet
                for qt in resp.includes["tweets"]:
                    if qt.id == ref.id:
                        return qt.conversation_id
    return tweet.conversation_id


def get_author_id(tweet_id: str) -> Optional[str]:
    """Get the author ID of a tweet.

    Args
    ----
    tweet_id: str
        The ID of the tweet to get the author ID of.

    Returns
    -------
    Optional[str]
        The author ID of the tweet, or None if the tweet doesn't exist.
    """
    resp = tw_client.get_tweet(tweet_id, tweet_fields=["author_id"])
    tweet: tweepy.Tweet = resp.data
    logger.debug("Thread start tweet: %s", tweet)
    return tweet.author_id


def get_conversation_tweets(conv_id: str, author_id: str) -> tweepy.Response:
    """Get the tweets in a conversation.

    Args
    ----
    conv_id: str
        The ID of the conversation to get the tweets of.
    author_id: str
        The ID of the author of the conversation.

    Returns
    -------
    tweepy.Response
        The response from the Twitter API.
    """

    curr_time = datetime.datetime.utcnow()
    end_time = curr_time - datetime.timedelta(seconds=10)
    end_time_str = end_time.isoformat("T", timespec="seconds") + "Z"

    resp = tw_client.search_recent_tweets(
        f"from:{author_id} to:{author_id} conversation_id:{conv_id}",
        max_results=100,
        tweet_fields=["referenced_tweets"],
        expansions=["referenced_tweets.id"],
        end_time=end_time_str,
    )
    while resp.meta["result_count"] == 0:
        time.sleep(3)  # Wait for API to catch up
        resp = tw_client.search_recent_tweets(
            f"from:{author_id} to:{author_id} conversation_id:{conv_id}",
            max_results=100,
            tweet_fields=["referenced_tweets"],
            expansions=["referenced_tweets.id"],
            end_time=end_time_str,
        )
    return resp


def get_tweet_thread(conv_id: str, tagging_id: str) -> Optional[str]:
    """Get the thread of a tweet.

    Args
    ----
    conv_id: str
        The ID of the conversation to get the thread of.
    tagging_id: str
        The ID of the tweet that tagged the bot.

    Returns
    -------
    Optional[str]
        The thread of the tweet, or None if the tweet doesn't exist.
    """

    author_id = get_author_id(conv_id)
    if author_id is None:
        return None

    data = get_conversation_tweets(conv_id, author_id)
    if data is None:
        return None
    logger.debug("Tweet thread data: %s", data.data)

    tweets = {x.id: x.text for x in data.includes["tweets"]}
    tweets[data.data[0].id] = data.data[0].text
    parents = {
        x.id: next((y.id for y in x.referenced_tweets if y.type == "replied_to"), None)
        for x in data.data
    }
    conversation = [data.data[0].id]
    while conversation[-1] in parents and parents[conversation[-1]] is not None:
        conversation.append(parents[conversation[-1]])
    # Check whether the original tweet is present
    if conv_id not in conversation:
        conversation.append(conv_id)
    # We need to ignore the tagging tweet, in case we're tagged by the thread author.
    conversation = [tweets[x] for x in reversed(conversation) if x != tagging_id]

    thread = "\n".join(conversation)
    return thread


def get_gpt_summary(thread: str) -> str:
    """Get a summary of a thread using GPT-3.

    Args
    ----
    thread: str
        The thread to summarize.

    Returns
    -------
    str
        The summary of the thread.
    """
    try:
        summary = (
            openai.Completion.create(
                model="text-davinci-003",
                prompt=f"{thread}\n\nSummarize the above into a single 280 character Tweet:",  # noqa: E501
                temperature=0.7,
                max_tokens=70,
            )
            .choices[0]
            .text.strip()
        )
    except openai.OpenAIError as e:
        logger.error(
            "OpenAI request returned status %d with error: %s",
            e.http_status,
            e.user_message,
        )
        return ""
    return summary


def limit_summary(summary: str) -> str:
    """Limit the summary to 280 characters.

    Args
    ----
    summary: str
        The summary to limit.

    Returns
    -------
    str
        The summary, constrained to 280 characters.
    """
    summary.replace("@GPTSummary", "GPTSummary")
    if len(summary) > 280:
        cur_len = len(summary)
        removed = 0
        words = summary.split()
        for i in range(len(words) - 1, 0, -1):
            removed += len(words[i]) + 1
            if cur_len - removed <= 280:
                summary = " ".join(words[:i])[:280]
                break
    return summary


def get_conversation_summary(tweet_id: str) -> Optional[str]:
    conv_id = get_conversation_id(tweet_id)
    if conv_id is None:
        return None
    thread = get_tweet_thread(conv_id, tweet_id)
    if thread is None:
        return None
    logger.info(f"Thread:\n\n{thread}")
    summary = get_gpt_summary(thread)
    if not summary:
        return None
    logger.info(f"GPT summary:\n\n{summary}")
    summary = limit_summary(summary)
    logger.info(f"Reponse tweet:\n\n{summary}")
    return summary


def reply_to_user(tweet_id: str, summary: str):
    tw_client.create_tweet(text=summary, in_reply_to_tweet_id=tweet_id)
