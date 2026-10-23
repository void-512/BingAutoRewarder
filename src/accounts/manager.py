"""Account index and metadata. Browser erasure is coordinated by deletion.py."""

from datetime import datetime
import uuid

from ..config import ACCOUNTS_INDEX_PATH, validate_account_id
from ..storage import read_json, write_json
from .meta import AccountMetaManager


class AccountManager:
    def __init__(self, global_settings):
        self.settings = global_settings

    def read_index(self):
        accounts = read_json(ACCOUNTS_INDEX_PATH, [])
        if not isinstance(accounts, list):
            raise ValueError(
                "accounts.json must contain an array; existing data was left intact"
            )
        for account in accounts:
            validate_account_id(account["id"])
        return accounts

    def write_index(self, accounts):
        write_json(ACCOUNTS_INDEX_PATH, accounts)

    def list(self):
        return [
            {
                **account,
                "first_setup_done": AccountMetaManager(
                    account["id"]
                ).is_first_setup_done(),
            }
            for account in self.read_index()
        ]

    def get(self, account_id):
        validate_account_id(account_id)
        return next(
            (account for account in self.list() if account["id"] == account_id), None
        )

    def resolve(self, token):
        accounts = self.list()
        matches = [account for account in accounts if account["id"] == token]
        if not matches:
            matches = [
                account
                for account in accounts
                if account["label"].strip().casefold() == token.strip().casefold()
            ]
        if len(matches) != 1:
            raise ValueError(
                "Unknown or ambiguous account; use an id from --list-accounts"
            )
        return matches[0]

    def create(self, label):
        if not label.strip():
            raise ValueError("Account label must not be empty")
        accounts = self.read_index()
        account = {
            "id": uuid.uuid4().hex[:12],
            "label": label.strip(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        accounts.append(account)
        AccountMetaManager(account["id"]).save_meta({"first_setup_done": False})
        self.write_index(accounts)
        return {**account, "first_setup_done": False}
