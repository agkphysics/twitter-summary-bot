import datetime
import time
from collections import defaultdict
from typing import Any, Optional

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


def build_tweet_tree(parents: dict[int, int]) -> dict[int, list[int]]:
    """Build a tree of tweets.

    Args
    ----
    parents: dict[int, int]
        A dictionary mapping tweet IDs to their parent IDs.

    Returns
    -------
    dict[int, list[int]]
        A dictionary mapping tweet IDs to a list of their children.
    """

    tree = defaultdict(list)
    for child, parent in parents.items():
        tree[parent].append(child)
    return tree


def enumerate_tweet_tree(tree: dict[int, list[int]], root: int) -> list[int]:
    """Enumerate a tree of tweets.

    Args
    ----
    tree: dict[int, list[int]]
        A dictionary mapping tweet IDs to a list of their children.
    root: int
        The ID of the root tweet.

    Returns
    -------
    list[int]
        A list of tweet IDs in the order they should be replied to.
    """

    if root not in tree:
        return [root]
    return [root] + [
        child
        for child in sorted(tree[root])
        for child in enumerate_tweet_tree(tree, child)
    ]


def get_parent(tweet: dict[str, Any]) -> Optional[int]:
    """Get the parent of a tweet.

    Args
    ----
    tweet: dict[str, Any]
        The tweet to get the parent of.

    Returns
    -------
    Optional[str]
        The ID of the parent tweet, or None if the tweet doesn't have a parent.
    """

    if "referenced_tweets" in tweet:
        for ref in tweet["referenced_tweets"]:
            if ref["type"] == "replied_to":
                return int(ref["id"])
    return None


def get_conversation_id(tweet_id: int) -> Optional[int]:
    """Get the conversation ID of a tweet.

    Args
    ----
    tweet_id: int
        The ID of the tweet to get the conversation ID of.

    Returns
    -------
    Optional[int]
        The conversation ID of the tweet, or None if the tweet doesn't exist.
    """

    resp = tw_client.get_tweet(
        tweet_id,
        tweet_fields=["conversation_id", "referenced_tweets"],
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


def get_start_tweet(tweet_id: int) -> tuple[Optional[int], str]:
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
    return tweet.author_id, tweet.text


def get_conversation_tweets(conv_id: int, author_id: int) -> tweepy.Response:
    """Get the tweets in a conversation.

    Args
    ----
    conv_id: int
        The ID of the conversation to get the tweets of.
    author_id: int
        The ID of the author of the conversation.

    Returns
    -------
    tweepy.Response
        The response from the Twitter API.
    """

    curr_time = datetime.datetime.utcnow()
    end_time = curr_time - datetime.timedelta(seconds=10)
    end_time_str = end_time.isoformat("T", timespec="seconds") + "Z"

    def _get_tweets() -> tweepy.Response:
        return tw_client.search_recent_tweets(
            f"from:{author_id} to:{author_id} conversation_id:{conv_id}",
            max_results=100,
            tweet_fields=["referenced_tweets", "conversation_id", "author_id"],
            expansions=["referenced_tweets.id"],
            end_time=end_time_str,
        )

    while (resp := _get_tweets()).meta["result_count"] == 0:
        time.sleep(3)  # Wait for API to catch up
    return resp


def get_tweet_thread(conv_id: int, tagging_id: int) -> Optional[list[str]]:
    """Get the thread of a tweet.

    Args
    ----
    conv_id: int
        The ID of the conversation to get the thread of.
    tagging_id: int
        The ID of the tweet that tagged the bot.

    Returns
    -------
    Optional[str]
        The thread of the tweet, or None if the tweet doesn't exist.
    """

    author_id, text = get_start_tweet(conv_id)
    if author_id is None:
        return None

    data = get_conversation_tweets(conv_id, author_id)
    if data is None:
        return None
    logger.debug("Tweet thread data: %s", data.data)

    # Need to get the included tweets, since sometimes the API doesn't return all
    # the tweets
    tweets: dict[int, str] = {conv_id: text}
    parents: dict[int, int] = {}
    for tweet in data.includes["tweets"] + data.data:
        if tweet.author_id == author_id and tweet.conversation_id == conv_id:
            tweets[tweet.id] = tweet.text
        if (p := get_parent(tweet)) is not None:
            parents[tweet.id] = p

    tree = build_tweet_tree(parents)
    logger.debug("Tweet tree: %s", tree)

    # We need to ignore the tagging tweet, in case we're tagged by the thread author.
    conversation = [
        tweets[x] for x in enumerate_tweet_tree(tree, conv_id) if x != tagging_id
    ]
    return conversation


def get_gpt_summary(thread: list[str]) -> str:
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
    prompt = "<tweet>" + "</tweet><tweet>".join(thread) + "</tweet>"
    prompt = (
        f"{prompt}\nSummarize the above into a single 280 character Tweet:\n<tweet>"
    )
    try:
        summary = (
            openai.Completion.create(
                model="text-davinci-003",
                prompt=prompt,
                temperature=0.7,
                max_tokens=70,
                stop="</tweet>",
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


def get_conversation_summary(tweet_id: int) -> Optional[str]:
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


def reply_to_user(tweet_id: int, summary: str):
    tw_client.create_tweet(text=summary, in_reply_to_tweet_id=tweet_id)
