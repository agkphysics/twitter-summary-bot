from collections import defaultdict
from typing import Any, Optional


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
    """Enumerate a tree of tweets. This automatically ignores
    disconnected tweets (which might occur if the author replies to
    themself as a reply to someone else).

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
