from flask import Flask, request, current_app
from cachetools import TTLCache
import requests
from .constants import *
from .queries import *
import urllib.parse
import json
import sqlite3

def _execute(con, *query):
    cur = con.execute(*query)
    cur.close()

def _fetch_one(con, *query):
    cur = con.execute(*query)
    row = cur.fetchone()
    cur.close()
    return row

def _fetch_all(con, *query):
    cur = con.execute(*query)
    rows = cur.fetchall()
    cur.close()
    return rows

def setup():
    con = current_app.connect()
    con.executescript(create_tables).close()
    row_count = _fetch_one(con, select_account_count)
    if(row_count["count"] == 0):
        print("No twitter accounts, so not hosting /twitter/*")
        return
    current_app.add_url_rule("/twitter/media", view_func=twitter_media)
    current_app.add_url_rule("/twitter/tweet", view_func=twitter_tweet)
    state = {
        "timeout": TTLCache(maxsize = 100, ttl = 300),
        "cache": TTLCache(maxsize = 2000, ttl = 900),
        "recache": {}
    }
    current_app.state["twitter"] = state

def _request(path, params, account):
    try:
        response = requests.get(
            f"https://api.twitter.com/graphql/{path}",
            params=params,
            cookies={
                "auth_token": account["auth_token"],
                "ct0": account["csrf_token"]
            },
            headers={
                "authorization": account["bearer_token"],
                "x-csrf-token": account["csrf_token"]
            }
        )
        if response.ok:
            return response.json(), None
        else:
            return response.json(), response.status_code
    except Exception as e:
        return None, 500


def _update_visibility(result, con, account_id, rest_id):
    if "blocked_by" in result["legacy"]:
        _execute(con, insert_blocks, (account_id, rest_id))
    else:
        _execute(con, delete_blocks, (account_id, rest_id))
    if "protected" in result["legacy"]:
        _execute(con, insert_privates, (rest_id,))
    else:
        _execute(con, delete_privates, (rest_id,))
    if "following" in result["legacy"]:
        _execute(con, insert_follows, (account_id, rest_id))
    else:
        _execute(con, delete_follows, (account_id, rest_id))
    return "blocked_by" not in result["legacy"] and ("following" in result["legacy"] or "protected" not in result["legacy"])

def _request_visibility(con, account, rest_id):
    response, error = _request(
        f"{user_by_rest_id_query_id}/UserByRestId",
        urllib.parse.urlencode({
            "variables": json.dumps(
                user_by_rest_id_variables |
                {"userId": rest_id}
            ),
            "features" : json.dumps(user_by_rest_id_features)
        }),
        account
    )
    if error is None:
        if not response["data"]["user"]: 
            return None, "user does not exist"
        if _update_visibility(response["data"]["user"]["result"], con, account["account_id"], rest_id):
            return True, None
        else:
            return False, None
    else:
        if error == 429:
            current_app.state["twitter"]["timeout"][account["account_id"]] = True
        return None, response.status_code

def _request_media(con, account, rest_id):
    if account["account_id"] in current_app.state["twitter"]["timeout"]: return False
    response, error = _request(
        f"{user_media_query_id}/UserMedia",
        urllib.parse.urlencode({
            "variables": json.dumps(
                user_media_variables |
                {"userId": rest_id} |
                ({"cursor": request.args["cursor"]} if "cursor" in request.args else {})
            ),
            "features" : json.dumps(user_media_features)
        }),
        account
    )
    if error is None:
        if "debug" in request.args: return response, None
        if not response["data"]: return {"note": "user not found"}, 404
        if not response["data"]["user"]: #blocked or privated; recheck visibility
            visibility, error = _request_visibility(con, account, rest_id)
            if error is None:
                if visibility:
                    current_app.log(f"visibility returned unexpected result for account id {account['account_id']} and rest id {rest_id}")
                else:
                    return None, "invisible"
            else:
                return None, error
        if response["data"]["user"]["result"]["__typename"] == "UserUnavailable":
            return None, "UserUnavailable"
        instructions = response["data"]["user"]["result"]["timeline_v2"]["timeline"]["instructions"]
        entries = next(x["entries"] for x in instructions if x["type"] == "TimelineAddEntries")
        tweets = next((x["moduleItems"] for x in instructions if x["type"] == "TimelineAddToModule"),
            next((x["content"]["items"] for x in entries if x["content"]["__typename"] == "TimelineTimelineModule"),
            []))
        bottom_cursor = next(x for x in entries if x["content"]["__typename"] == "TimelineTimelineCursor" and x["content"]["cursorType"] == "Bottom")["content"]["value"]
        tweet_ids = []
        for item in tweets:
            if "result" not in item["item"]["itemContent"]["tweet_results"]: continue
            tweet = item["item"]["itemContent"]["tweet_results"]["result"]
            match tweet["__typename"]:
                case "Tweet":
                    pass
                case "TweetWithVisibilityResults":
                    tweet = tweet["tweet"]
                case "TweetUnavailable":
                    current_app.log(f"error reading unavailable tweet at rest id {rest_id} and cursor {request.args['cursor']}")
                    current_app.log(json.dumps(tweet))
                    continue
                case "TweetTombstone":
                    current_app.log(f"error reading circle tweet at rest id {rest_id} and cursor {request.args['cursor']}")
                    current_app.log(json.dumps(tweet))
                    continue
                case _:
                    current_app.log(f"Unexpected json structure in response at rest id {rest_id} and cursor {request.args['cursor']}")
                    current_app.log(json.dumps(tweet))
                    continue
            tweet_id = tweet["rest_id"]
            if "locked" in request.args:
                current_app.state["twitter"]["cache"][tweet_id] = tweet
                current_app.state["twitter"]["recache"][tweet_id] = (current_app.state["twitter"]["user_ids"][username], request.args["cursor"] if "cursor" in request.args else None)
            tweet_ids.append(tweet_id)
        return {"tweet_ids": tweet_ids, "next_page": f"media?username={request.args['username']}&cursor={bottom_cursor}"}, None
    else:
        if error == 429:
            current_app.state["twitter"]["timeout"][account["account_id"]] = True
        return None, error

def twitter_media():
    with current_app.connect() as con:
        username = request.args["username"]
        rest_id = None

        #get rest_id for username
        res = _fetch_one(con, select_rest_id, (username,))
        if res is None:
            accounts = _fetch_all(con, select_all_accounts)
            for account in accounts:
                if account["account_id"] in current_app.state["twitter"]["timeout"]: continue
                response, error = _request(
                    f"{user_by_screen_name_query_id}/UserByScreenName",
                    urllib.parse.urlencode({
                        "variables": json.dumps(
                            user_by_screen_name_variables |
                            {"screen_name": username}
                        ),
                        "features" : json.dumps(user_by_screen_name_features)
                        }),
                    account
                )
                if error is None:
                    if not response["data"]: return {"note": "user not found"}, 404
                    match response["data"]["user"]["result"]["__typename"]:
                        case "User":
                            rest_id = response["data"]["user"]["result"]["rest_id"]
                            _execute(con, insert_rest_id, (username, rest_id))
                            _update_visibility(response["data"]["user"]["result"], con, account["account_id"], rest_id)
                            break
                        case "UserUnavailable":
                            return {"note": response["data"]["user"]["result"]["message"]}
                        case _:
                            current_app.log(f"Unexpected structure {response['data']['user']['result']['__typename']} in UserByScreenName response for user {username}")
                elif error == 429:
                    current_app.state["twitter"]["timeout"][account["account_id"]] = True
                    continue
                else:
                    return {"note": str(error)}
        else:
            rest_id = res["rest_id"]

        #get valid accounts for user & attempt to query
        accounts = _fetch_all(con, select_accounts_for_creator, (rest_id,))
        valid_accounts = [account for account in accounts if account["validity"]]
        for account in valid_accounts:
            response, error = _request_media(con, account, rest_id)
            if error is None:
                return response
            else:
                match error:
                    case 429:
                        continue
                    case "invisible":
                        continue
                    case "UserUnavailable":
                        return {"note": str(error)}
                    case _:
                        current_app.log(error, " at ", username, ", ", request.args["cursor"] if "cursor" in request.args else "(no cursor)")
                        return {"note": str(error)}
        
        current_app.log(f"accounts for {rest_id} blocked!")

        #the account we thought was valid is no longer valid, recheck all other accounts before giving up
        other_accounts = [account for account in accounts if not account["validity"]]
        for account in other_accounts:
            visibility, error = _request_visibility(con, account, rest_id)
            if visibility:
                response, error = _request_media(con, account, rest_id)
                if error is None:
                    return response
                else:
                    match error:
                        case 429:
                            continue
                        case "invisible":
                            continue
                        case _:
                            current_app.log(error, " at ", username, ", ", request.args["cursor"] if "cursor" in request.args else "(no cursor)")
                            return {"note": str(error)}
            else:
                return {"note": str(error)}
        current_app.log(f"no accounts for {rest_id} were successful!")

        return {"note": "account blocked or protected"}

def twitter_tweet():
    with current_app.connect() as con:
        tweet_id = request.args["tweet"]
        local_idx = current_app.state["twitter"]["idx"]
        if tweet_id in current_app.state["twitter"]["cache"]: return current_app.state["twitter"]["cache"].pop(tweet_id)
        elif tweet_id in current_app.state["twitter"]["recache"]:
            user_id, cursor = current_app.state["twitter"]["recache"][tweet_id]
            response, error = _request(
                f"{user_media_query_id}/UserMedia",
                urllib.parse.urlencode({
                    "variables": json.dumps(
                        user_media_variables |
                        {"userId": user_id} |
                        ({"cursor": cursor} if cursor else {})
                    ),
                    "features" : json.dumps(user_media_features)
                }),
                local_idx
            )
            if error is None:
                entries = response["data"]["user"]["result"]["timeline_v2"]["timeline"]["instructions"][0]["entries"]
                tweet_entries = [entry for entry in entries if (entry["content"]["__typename"] == "TimelineTimelineItem" and entry["content"]["itemContent"]["tweet_results"])]
                bottom_cursor = entries[-1]["content"]["value"]
                for entry in tweet_entries:
                    if "result" not in entry["content"]["itemContent"]["tweet_results"]: continue
                    tweet = entry["content"]["itemContent"]["tweet_results"]["result"]
                    match tweet["__typename"]:
                        case "Tweet": 
                            current_app.state["twitter"]["cache"][tweet_id] = tweet
                        case "TweetWithVisibilityResults":
                            current_app.state["twitter"]["cache"][tweet_id] = tweet["tweet"]
                        case "TweetUnavailable":
                            current_app.log(f"error reading protected tweet: {tweet_id}")
                        case _:
                            current_app.log("Unexpected json structure in response!")
                            current_app.log(response.json().dumps())
            if tweet_id in current_app.state["twitter"]["cache"]: return current_app.state["twitter"]["cache"].pop(tweet_id)
        response, error = _request(
            f"{tweet_query_id}/TweetResultByRestId",
            urllib.parse.urlencode({
                "variables": json.dumps(
                    tweet_variables |
                    {"tweetId": tweet_id}
                ),
                "features" : json.dumps(tweet_features)
            }),
            local_idx
        )
        if error is None:
            if "debug" in request.args: return response_json
            match response["data"]["tweetResult"]["result"]["__typename"]:
                case "Tweet": return response["data"]["tweetResult"]["result"]
                case "TweetWithVisibilityResults": return response["data"]["tweetResult"]["result"]["tweet"]
                case "TweetUnavailable":
                    current_app.log(f"error reading protected tweet: {tweet_id}")
                    return {"note": result["reason"]}, error
                case _:
                    current_app.log("Unexpected json structure in response!")
                    current_app.log(response)
                    return response
        else:
            return {"note": str(error)}, error