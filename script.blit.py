"""
WhaleSwap.blit - Minimal AMM on Blitchain

"""

import json
import math
from decimal import Decimal
from string import Template

from blit import (_send_msg, emit_event, get_attached_messages, get_block_info,
                  get_caller_address, get_script_address, send_query)

BLOCK_HEIGHT = get_block_info()["height"]
BLOCK_TIME = get_block_info()["time"]
CALLER = get_caller_address()
SCRIPT_ADDRESS = get_script_address()


coins_sent = {}
for m in get_attached_messages():
    if (
        m["msg"]["@type"] == "/cosmos.bank.v1beta1.MsgSend"
        and m["msg"]["to_address"] == SCRIPT_ADDRESS
    ):
        for a in m["msg"]["amount"]:
            coins_sent[a["denom"]] = Decimal(a["amount"]) + coins_sent.get(
                a["denom"], 0
            )
coins = sorted(
    [{"amount": str(v), "denom": k} for k, v in coins_sent.items()],
    key=lambda x: x["denom"],
)
del coins_sent

NAME = SCRIPT_ADDRESS


def _get_pool_index(pool_id: int) -> str:
    return f"pools/{int(pool_id):015}"


def _get_next_pool_id():
    index = f"next_pool_id"
    try:
        res = send_query(
            "/blit.storage.Query/StorageDetail", address=SCRIPT_ADDRESS, index=index
        )
        next_id = int(res["storage"]["data"])
    except Exception as e:
        if "not found" in str(e):
            next_id = 1
        else:
            raise e
    result = _send_msg(
        "/blit.storage.MsgUpdateStorage",
        address=SCRIPT_ADDRESS,
        index=index,
        data=str(next_id + 1),
        grantee=SCRIPT_ADDRESS,
        force=True,
    )
    return next_id


def get_pool(pool_id: int):
    try:
        result = send_query(
            "/blit.storage.Query/StorageDetail",
            address=SCRIPT_ADDRESS,
            index=_get_pool_index(pool_id),
        )
    except Exception as e:
        if "not found" in str(e):
            raise Exception(f"Pool {pool_id} not found")
        raise e
    pool = json.loads(
        result["storage"]["data"],
        parse_float=Decimal,
        parse_int=Decimal,
    )
    return pool


def get_shares_denom(pool_id: int):
    return f"blit/{NAME}/pool-{pool_id}"


def create_pool():
    assert len(coins) == 2, "Both base and quote (BLT) must be sent to create the pool"

    # set base and quote coins, the quote is the denominated DYS
    if coins[0]["denom"] == "ublit":
        base_coin = coins[1]
        quote_coin = coins[0]
    elif coins[1]["denom"] == "ublit":
        base_coin = coins[0]
        quote_coin = coins[1]
    else:
        raise Exception("No BLT coins sent, cannot create pool.")

    pool_id = _get_next_pool_id()
    pool_index = _get_pool_index(pool_id)

    initial_shares = Decimal("100000")
    shares_denom = get_shares_denom(pool_id)
    pool = {
        "pool_id": pool_id,
        "base": {
            "denom": base_coin["denom"],
            "balance": Decimal(base_coin["amount"]),
            "lent": 0,
            "collateral": 0,
        },
        "quote": {
            "denom": quote_coin["denom"],
            "balance": Decimal(quote_coin["amount"]),
            "lent": 0,
            "collateral": 0,
        },
        "total_shares": initial_shares,
        "shares_denom": shares_denom,
        "block_height": BLOCK_HEIGHT,
        "created": BLOCK_TIME,
    }
    emit_event(key="poolupdate", value=str(pool_id))

    _send_msg(
        "/blit.storage.MsgCreateStorage",
        address=SCRIPT_ADDRESS,
        index=pool_index,
        data=json.dumps(pool),
        grantee=SCRIPT_ADDRESS,
    )

    _send_msg(
        "/blit.blit.MsgMintCoins",
        amount={
            "amount": str(initial_shares),
            "denom": shares_denom,
        },
        grantee=SCRIPT_ADDRESS,
    )

    _send_msg(
        "/cosmos.bank.v1beta1.MsgSend",
        from_address=SCRIPT_ADDRESS,
        to_address=CALLER,
        amount=[{"amount": str(initial_shares), "denom": shares_denom}],
    )

    return pool


def join_pool(pool_id: str):
    assert len(coins) == 2, "Both base and quote (BLIT) must be sent to join the pool"

    # set base and quote coins, the quote is the denominated DYS
    if coins[0]["denom"] == "ublit":
        base_coin = coins[1]
        quote_coin = coins[0]
    elif coins[1]["denom"] == "ublit":
        base_coin = coins[0]
        quote_coin = coins[1]
    else:
        raise Exception("No DYS coins sent, cannot join pool.")

    pool = get_pool(pool_id)

    sent_base_amount = Decimal(base_coin["amount"])
    sent_quote_amount = Decimal(quote_coin["amount"])

    pool_base = pool["base"]
    pool_quote = pool["quote"]

    correct_base_amount = math.ceil(
        (sent_quote_amount * Decimal(pool_base["balance"])) / (pool_quote["balance"])
    )
    correct_quote_amount = math.ceil(
        (sent_base_amount * Decimal(pool_quote["balance"])) / (pool_base["balance"])
    )
    refund = []
    if sent_base_amount > correct_base_amount:
        refund_amount = sent_base_amount - correct_base_amount
        refund_denom = base_coin["denom"]

        _send_msg(
            "/cosmos.bank.v1beta1.MsgSend",
            from_address=SCRIPT_ADDRESS,
            to_address=CALLER,
            amount=[{"amount": str(refund_amount), "denom": refund_denom}],
        )
        refund += [{"amount": refund_amount, "denom": refund_denom}]

    if sent_quote_amount > correct_quote_amount:
        refund_amount = sent_quote_amount - correct_quote_amount
        refund_denom = quote_coin["denom"]
        _send_msg(
            "/cosmos.bank.v1beta1.MsgSend",
            from_address=SCRIPT_ADDRESS,
            to_address=CALLER,
            amount=[{"amount": str(refund_amount), "denom": refund_denom}],
        )
        refund += [{"amount": refund_amount, "denom": refund_denom}]

    # update pool balances
    pool["base"]["balance"] = (pool_base["balance"]) + sent_base_amount
    pool["quote"]["balance"] = (pool_quote["balance"]) + sent_quote_amount
    pool["updated"] = BLOCK_TIME
    # calculate shares
    base_shares = (sent_base_amount * pool["total_shares"]) // pool_base["balance"]
    quote_shares = (sent_quote_amount * pool["total_shares"]) // pool_quote["balance"]

    # in case there is a rounding difference, give the smaller share amount
    shares = min(base_shares, quote_shares)

    pool_index = _get_pool_index(pool_id)
    shares_denom = get_shares_denom(pool_id)

    _send_msg(
        "/blit.blit.MsgMintCoins",
        amount={
            "amount": str(shares),
            "denom": shares_denom,
        },
        grantee=SCRIPT_ADDRESS,
    )
    pool["total_shares"] = Decimal(pool["total_shares"]) + shares

    _send_msg(
        "/cosmos.bank.v1beta1.MsgSend",
        from_address=SCRIPT_ADDRESS,
        to_address=CALLER,
        amount=[{"amount": str(shares), "denom": shares_denom}],
    )

    _send_msg(
        "/blit.storage.MsgUpdateStorage",
        address=SCRIPT_ADDRESS,
        index=pool_index,
        data=json.dumps(pool),
        grantee=SCRIPT_ADDRESS,
    )

    # pool_id, shares, refunded amount and denom
    return {
        "pool_id": pool_id,
        "shares": shares,
        "share_denom": shares_denom,
        "refund": refund,
    }


def exit_pool(pool_id: str):
    assert len(coins) == 1, "Only the shares denom must be sent to exit the pool"

    shares_denom = coins[0]["denom"]
    sent_shares_amount = Decimal(coins[0]["amount"])

    needed_denom = get_shares_denom(pool_id)
    assert (
        shares_denom == needed_denom
    ), f"Invalid shares denom, sent [{shares_denom}] needed [{needed_denom}] "

    pool = get_pool(pool_id)

    result = send_query("/cosmos.bank.v1beta1.Query/SupplyOf", denom=shares_denom)
    total_shares = result["amount"]
    total_shares_amount = Decimal(total_shares["amount"])

    # assert the total shores matches the pool total shares .
    # this shouldn't happen.
    assert (
        total_shares_amount == pool["total_shares"]
    ), f"Total shares mismatch, pool {pool['total_shares']} != total {total_shares_amount}"

    base_amount = (
        sent_shares_amount * Decimal(pool["base"]["balance"])
    ) // total_shares_amount
    quote_amount = (
        sent_shares_amount * Decimal(pool["quote"]["balance"])
    ) // total_shares_amount

    _send_msg(
        "/blit.blit.MsgBurnCoins",
        amount={
            "amount": str(sent_shares_amount),
            "denom": shares_denom,
        },
        grantee=SCRIPT_ADDRESS,
    )

    pool["total_shares"] = Decimal(pool["total_shares"]) - sent_shares_amount

    # update the base and denom on the pool
    pool["base"]["balance"] = Decimal(pool["base"]["balance"]) - base_amount
    pool["quote"]["balance"] = Decimal(pool["quote"]["balance"]) - quote_amount
    pool["updated"] = BLOCK_TIME

    amount = []

    if base_amount:
        amount += [
            {"amount": str(base_amount), "denom": pool["base"]["denom"]},
        ]
    if quote_amount:
        amount += [
            {"amount": str(quote_amount), "denom": pool["quote"]["denom"]},
        ]

    if not quote_amount and not base_amount:
        raise Exception(
            f"Shares [{sent_shares_amount} {shares_denom}] value to small to exchange"
        )
    amount = sorted(
        amount,
        key=lambda x: x["denom"],
    )

    _send_msg(
        "/cosmos.bank.v1beta1.MsgSend",
        from_address=SCRIPT_ADDRESS,
        to_address=CALLER,
        amount=amount,
    )
    emit_event(poolupdate=str(pool_id))

    _send_msg(
        "/blit.storage.MsgUpdateStorage",
        address=SCRIPT_ADDRESS,
        index=_get_pool_index(pool_id),
        data=json.dumps(pool),
        grantee=SCRIPT_ADDRESS,
    )
    return amount


def swap(pool_ids: str, minimum_swap_out_amount: int, swap_out_denom: str):
    assert len(coins) == 1, "One and only one coin denom must be sent for swapping"

    pool_ids = str(pool_ids)
    minimum_swap_out_amount = Decimal(minimum_swap_out_amount)
    input_amount = Decimal(coins[0]["amount"])
    input_denom = coins[0]["denom"]

    for pool_id in pool_ids.split():
        pool = get_pool(pool_id)

        K = pool["base"]["balance"] * pool["quote"]["balance"]

        if input_denom == pool["base"]["denom"]:
            pool["base"]["balance"] += input_amount
            output_amount = math.floor(
                pool["quote"]["balance"] - (K / pool["base"]["balance"])
            )
            assert output_amount, "Swap size too small"
            pool["quote"]["balance"] -= output_amount
            assert pool["quote"]["balance"] > 0, "Swap size too large"
            output_denom = pool["quote"]["denom"]
        elif input_denom == pool["quote"]["denom"]:
            pool["quote"]["balance"] += input_amount
            output_amount = math.floor(
                pool["base"]["balance"] - (K / pool["quote"]["balance"])
            )
            assert output_amount, "Swap size too small"
            pool["base"]["balance"] -= output_amount
            assert pool["base"]["balance"] > 0, "Swap size too large"
            output_denom = pool["base"]["denom"]
        else:
            raise Exception(
                f'input denom must be one of : [{pool["base"]["denom"]}, {pool["quote"]["denom"]}]'
            )
        input_denom = output_denom
        input_amount = output_amount
        pool["updated"] = BLOCK_TIME
        pool["num_trades"] = 1 + pool.get("num_trades", 0)

        emit_event(key="poolupdate", value=str(pool_id))
        _send_msg(
            "/blit.storage.MsgUpdateStorage",
            address=SCRIPT_ADDRESS,
            index=_get_pool_index(pool_id),
            data=json.dumps(pool),
            grantee=SCRIPT_ADDRESS,
        )

    if output_amount < minimum_swap_out_amount:
        raise Exception(
            f"Slippage occured, minimum output amount not reached: {output_amount} {output_denom} < {minimum_swap_out_amount} {output_denom}"
        )
    if swap_out_denom != output_denom:
        raise Exception(
            f"Output denom doesn't match, wanted: {swap_out_denom} got: {output_denom}"
        )


    _send_msg(
        "/cosmos.bank.v1beta1.MsgSend",
        from_address=SCRIPT_ADDRESS,
        to_address=CALLER,
        amount=[{"amount": str(output_amount), "denom": output_denom}],
    )
    return {"output_amount": output_amount, "output_denom": output_denom}


DEFAULT_VERSION = "v1.0.0"


def parse_cookies(cookie_str):
    cookies = {}
    for item in cookie_str.split("; "):
        if "=" in item:
            key, value = item.split("=", 1)
            cookies[key] = value
    return cookies


def parse_qs(qs):
    parameters = {}
    for item in qs.split("&"):
        if "=" in item:
            key, value = item.split("=", 1)
            parameters[key] = value
    return parameters


def wsgi(environ, start_response):
    # parse query string and cookies
    parameters = parse_qs(environ.get("QUERY_STRING", ""))
    cookies = parse_cookies(environ.get("HTTP_COOKIE", ""))

    # get the version from the query string, cookie, or default
    version = parameters.get("version", cookies.get("version", DEFAULT_VERSION))
    headers = [
        ("Content-type", "text/html; charset=UTF-8"),
        ("Set-Cookie", f"version={version}; Path=/; HttpOnly"),
    ]

    path_info = environ.get("PATH_INFO", "")
    if path_info.startswith("/assets/"):
        status = "302 Found"
        headers += [
            (
                "Location",
                f"https://cdn.jsdelivr.net/gh/sybilsingleton/whaleswap.dys@{version}/dist/assets/"
                + path_info[len("/assets/") :],
            ),
        ]
        start_response(status, headers)
        return []

    status = "200 OK"

    # Prepare the JavaScript code for fetching the manifest file
    manifest_url = f""

    start_response(status, headers)

    return [f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>WhaleSwap.dys</title>
    <script src="/_/dyson.js"></script>
    <script type="module" src=""></script>
    <link rel="stylesheet" href="">
  </head>
  <body class="container mx-auto" id="app">
  </body>
    <script>
        document.addEventListener('DOMContentLoaded', (event) => {{
            fetch('https://cdn.jsdelivr.net/gh/sybilsingleton/whaleswap.dys@{version}/dist/manifest.json')
                .then(response => response.json())
                .then(data => {{
                    let mainJS = 'https://cdn.jsdelivr.net/gh/sybilsingleton/whaleswap.blit@{version}/dist/' + data["src/main.js"]["file"];
                    let mainCSS = 'https://cdn.jsdelivr.net/gh/sybilsingleton/whaleswap.blit@{version}/dist/' + data["src/main.css"]["file"];

                    // Set sources
                    document.querySelector('link[rel="stylesheet"]').href = mainCSS;
                    
                    // Create script tag dynamically
                    let script = document.createElement('script');
                    script.type = 'module';
                    script.src = mainJS;
                    document.head.appendChild(script);
                }});
        }});
    </script>
</html>"""
.encode()]
