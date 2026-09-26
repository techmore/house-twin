"""
auth.py — one-time authorisation CLI for Aqara Account Authorization Mode.

Server-to-server; there is no browser redirect and no ``redirect_uri`` to
configure. You supply your Aqara account, Aqara emails you a code, you paste it
back, and the token pair is written to ``config.json``.

    # once, after registering an app in the Aqara developer console:
    python -m house_twin.auth setup --app-id ... --app-key ... --key-id ... --region usa

    # then authorise:
    python -m house_twin.auth login you@example.com

    # later, if the token has expired:
    python -m house_twin.auth refresh

Token lifetimes are capped at 30 days by Aqara, and the refresh token outlives
the access token by a further 30 days. The poller refreshes automatically, so
this is only needed for the first authorisation and the occasional manual nudge.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .client import AqaraClient, AqaraError
from .config import Config, api_base


def cmd_setup(args: argparse.Namespace, config: Config) -> int:
    """Store the AppId / AppKey / KeyId issued by the developer console."""
    app_id = args.app_id or input("Appid: ").strip()
    app_key = args.app_key or getpass.getpass("AppKey (hidden): ").strip()
    key_id = args.key_id or input("Keyid: ").strip()

    if not (app_id and app_key and key_id):
        print("Appid, AppKey and Keyid are all required.", file=sys.stderr)
        return 1

    config.set_credentials(app_id, app_key, key_id, region=args.region)
    print(f"Credentials stored in {config.path}")
    print(f"Endpoint: {api_config_line(config)}")
    return 0


def api_config_line(config: Config) -> str:
    return api_base(config.region())


def cmd_login(args: argparse.Namespace, config: Config) -> int:
    """Run the two interactive steps: request a code, then exchange it."""
    if not config.has_credentials():
        print("No credentials stored. Run `auth setup` first.", file=sys.stderr)
        return 1

    account = args.account or input("Aqara account (email or phone): ").strip()
    if not account:
        print("An account is required.", file=sys.stderr)
        return 1

    client = AqaraClient(config)
    try:
        client.request_auth_code(account, validity=args.validity)
    except AqaraError as exc:
        print(f"Could not request an auth code: {exc}", file=sys.stderr)
        return 1

    print(f"\nAn authorisation code was sent to {account}.")
    print("It is valid for 10 minutes. Check your email or SMS.\n")

    entered = (args.auth_code or input("Auth code: ")).strip()
    if not entered:
        print("No code entered.", file=sys.stderr)
        return 1

    try:
        result = client.exchange_auth_code(account, entered)
    except AqaraError as exc:
        print(f"Code exchange failed: {exc}", file=sys.stderr)
        return 1

    expires_in = int(result.get("expiresIn") or 0)
    print(f"\nAuthorised. openId={result.get('openId')}")
    print(f"Access token valid for {expires_in // 3600} hours.")
    print(f"Tokens written to {config.path}")
    return 0


def cmd_refresh(args: argparse.Namespace, config: Config) -> int:
    """Renew the token pair from the stored refresh token."""
    if not config.has_token():
        print("Nothing to refresh — authorise first.", file=sys.stderr)
        return 1
    try:
        result = AqaraClient(config).refresh()
    except AqaraError as exc:
        print(f"Refresh failed: {exc}", file=sys.stderr)
        print("The refresh token may have expired; run `auth login` again.", file=sys.stderr)
        return 1
    print(f"Refreshed. Valid for {int(result.get('expiresIn') or 0) // 3600} hours.")
    return 0


def cmd_status(args: argparse.Namespace, config: Config) -> int:
    """Report whether we are ready to poll, without printing any secret."""
    print(f"config file : {config.path}")
    print(f"endpoint    : {api_config_line(config)}")
    print(f"credentials : {'yes' if config.has_credentials() else 'NO'}")
    print(f"authorised  : {'yes' if config.has_token() else 'NO'}")
    if config.has_token():
        remaining = config.token_expires_at() - int(__import__("time").time())
        state = "expired" if remaining <= 0 else f"{remaining // 3600}h remaining"
        print(f"token       : {state}")
    if not config.has_credentials():
        print("\nNext: python -m house_twin.auth setup")
    elif not config.has_token():
        print("\nNext: python -m house_twin.auth login <your aqara email>")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m house_twin.auth",
        description="Aqara Cloud OpenAPI v3.0 authorisation helper.",
    )
    parser.add_argument("--config", help="path to config.json")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="store developer-console credentials")
    p_setup.add_argument("--app-id")
    p_setup.add_argument("--app-key")
    p_setup.add_argument("--key-id")
    p_setup.add_argument("--region", default="usa", choices=["cn", "usa", "ger", "sg", "kr", "ru"])
    p_setup.set_defaults(func=cmd_setup)

    p_login = sub.add_parser("login", help="authorise an Aqara account")
    p_login.add_argument("account", nargs="?", help="email or phone number")
    p_login.add_argument("--auth-code", help="skip the interactive prompt")
    p_login.add_argument(
        "--validity", default="30d", help="token lifetime: 1h-24h or 1d-30d (default 30d)"
    )
    p_login.set_defaults(func=cmd_login)

    p_refresh = sub.add_parser("refresh", help="renew the access token")
    p_refresh.set_defaults(func=cmd_refresh)

    p_status = sub.add_parser("status", help="show readiness without secrets")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    config = Config(args.config)
    return args.func(args, config)


if __name__ == "__main__":
    sys.exit(main())
