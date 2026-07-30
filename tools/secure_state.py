#!/usr/bin/env python3
"""secure_state.py — Fernet-encrypted wrapper over rollout_shield.state.

Goal: zero data leak at rest. Sensitive state files (config.json,
reputation.json, claim signing blocks) are stored as Fernet tokens
that can only be decrypted when the owner has placed the unlock file
at ``.audit/.owner_unlock``.

Without the unlock file:
  * write attempts raise ``StateLockedError`` (refused)
  * read attempts return ``None`` (no data exposed)
  * no plaintext state files ever touch disk

Unlock file format:
  * 32 raw bytes OR a urlsafe-base64-encoded 32-byte Fernet key
  * mode 0600 (owner-read/write only)
  * gitignored — never committed

This is **defense in depth**, not a primary access control: a determined
attacker with root access can still read memory. But it stops:
  * accidental commits of state
  * cloud-synced backups syncing plaintext
  * third-party file scanners / cloud indexing
  * log aggregators that pick up JSONL files
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAS_FERNET = True
except Exception:  # pragma: no cover
    _HAS_FERNET = False


UNLOCK_PATH = Path(os.environ.get("ROLLOUT_SHIELD_UNLOCK", ".audit/.owner_unlock"))


class StateLockedError(RuntimeError):
    """Raised when an operation requires the owner unlock file and it's absent."""


def _read_unlock() -> bytes:
    """Read the Fernet key from the unlock file. Raise if missing/unreadable."""
    if not UNLOCK_PATH.exists():
        raise StateLockedError(
            f"owner unlock missing at {UNLOCK_PATH}; "
            f"refusing to operate on encrypted state. "
            f"Generate one with: python3 tools/secure_state.py --init"
        )
    try:
        data = UNLOCK_PATH.read_bytes()
        if len(data) == 44:
            # urlsafe-base64 32-byte key (Fernet format)
            return base64.urlsafe_b64decode(data)
        if len(data) == 32:
            return data
        # last resort: treat as utf-8 urlsafe base64
        return base64.urlsafe_b64decode(data.strip())
    except OSError as exc:
        raise StateLockedError(f"cannot read unlock at {UNLOCK_PATH}: {exc}")


def is_unlocked() -> bool:
    """Quick check: is the owner unlock present? Used by readers."""
    return UNLOCK_PATH.exists()


def _fernet() -> "Fernet":
    if not _HAS_FERNET:
        raise StateLockedError(
            "cryptography.fernet unavailable; install cryptography first"
        )
    key = _read_unlock()
    if len(key) != 32:
        raise StateLockedError(
            f"unlock key must be 32 raw bytes or urlsafe-base64; got {len(key)}"
        )
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_json(data: Any) -> bytes:
    """Encrypt a JSON-serializable value. Returns a Fernet token."""
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return _fernet().encrypt(payload)


def decrypt_json(token: bytes) -> Any:
    """Decrypt a Fernet token back into the original value."""
    try:
        payload = _fernet().decrypt(token)
    except InvalidToken as exc:
        raise StateLockedError(
            f"unlock does not match encrypted state at this path; "
            f"if state was encrypted with a different key, it is unrecoverable"
        ) from exc
    return json.loads(payload)


def encrypted_atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to disk as a Fernet token (atomic: temp + rename)."""
    if not is_unlocked():
        raise StateLockedError(
            f"refusing to write encrypted state without owner unlock at {UNLOCK_PATH}"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise StateLockedError(f"cannot create {path.parent}: {exc}") from exc
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encrypt_json(data))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def encrypted_read_json(path: Path) -> Any | None:
    """Read encrypted JSON. Returns None if unlock missing (no info leak)."""
    if not is_unlocked():
        return None
    if not path.exists():
        return None
    return decrypt_json(path.read_bytes())


@contextlib.contextmanager
def unlocked_only():
    """Context manager: code inside only runs when the unlock is present.

    Use to gate sensitive operations::

        with unlocked_only():
            state.save_reputation({...})
    """
    if not is_unlocked():
        raise StateLockedError(
            f"operation requires owner unlock at {UNLOCK_PATH}"
        )
    yield


def cmd_init(args: argparse.Namespace) -> int:  # type: ignore[name-defined]
    """Generate a new 32-byte unlock key. DESTRUCTIVE — overwrites existing."""
    import argparse
    parser = argparse.ArgumentParser(prog="secure_state --init")
    if UNLOCK_PATH.exists() and not args.force:
        print(f"unlock already exists at {UNLOCK_PATH}; pass --force to overwrite",
              file=__import__("sys").stderr)
        return 1
    UNLOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key() if _HAS_FERNET else os.urandom(32)
    UNLOCK_PATH.write_bytes(key)
    os.chmod(UNLOCK_PATH, 0o600)
    print(f"unlock written: {UNLOCK_PATH}")
    print(f"  bytes:  {len(key)}")
    print(f"  mode:   0600 (owner-rw only)")
    print()
    print("BACKUP THIS KEY OFFLINE. Loss of this key = loss of all encrypted state.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:  # type: ignore[name-defined]
    import argparse, sys
    parser = argparse.ArgumentParser(prog="secure_state --status")
    if UNLOCK_PATH.exists():
        print(f"unlock:    present at {UNLOCK_PATH}")
        try:
            st = UNLOCK_PATH.stat()
            print(f"  size:    {st.st_size} bytes")
            print(f"  mode:    {oct(st.st_mode & 0o777)}")
            print(f"  uid/gid: {st.st_uid}:{st.st_gid}")
            if (st.st_mode & 0o077) != 0:
                print("  WARNING: unlock is group/world readable — fix with chmod 600")
        except OSError as exc:
            print(f"  ERROR: {exc}")
    else:
        print(f"unlock:    ABSENT at {UNLOCK_PATH}")
        print("  state is encrypted-at-rest; operations will refuse until unlock is created")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse, sys
    p = argparse.ArgumentParser(prog="secure_state", description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--init", action="store_true", help="generate new unlock key")
    g.add_argument("--init-force", dest="init_force", action="store_true",
                   help="overwrite existing unlock key (DESTRUCTIVE)")
    g.add_argument("--status", action="store_true", help="show unlock status")
    g.add_argument("--check", action="store_true", help="exit 0 if unlocked, 1 otherwise")
    g.add_argument("--backup", action="store_true",
                   help="print 32-word paper backup phrase (use: --backup)")
    g.add_argument("--recover", action="store_true",
                   help="recover unlock from 32-word phrase (use: --recover w1 w2 ... w32)")
    g.add_argument("--verify-phrase", dest="verify_phrase", action="store_true",
                   help="verify a phrase matches the current unlock (use: --verify-phrase w1 w2 ... w32)")
    args, remaining = p.parse_known_args(argv)
    if args.backup:
        return cmd_backup(args)
    if args.recover:
        return cmd_recover(args, words=remaining)
    if args.verify_phrase:
        return cmd_verify_phrase(args, words=remaining)
    if args.init or args.init_force:
        ns = argparse.Namespace(force=args.init_force)
        return cmd_init(ns)
    if args.status:
        return cmd_status(args)
    if args.check:
        return 0 if is_unlocked() else 1
    return 1


# ---------- backup / recovery (BIP-39-style 24-word paper sheet) ----------
#
# We don't pull in `mnemonic` or any third-party lib. Instead, we encode
# the 32-byte key as a list of indices into a fixed 2048-word list, then
# map each index to its word. The 2048-word list is embedded below — a
# standard, well-known wordlist (same as BIP-39 English wordlist, public
# domain) so the words are unambiguous offline. We use ONLY a 256-entry
# subset to avoid embedding the full 2048 in this file; 256^24 ≈ 2^192,
# well above the key's 2^256 entropy — wait that's wrong direction.
#
# Actually: with 256 words × 24 positions = 8 bits per word × 24 = 192 bits.
# The key is 256 bits. So 256 words × 32 positions = 256 bits. Use 32 words.
#
# For paper backup, we use 32 words from the 256-word subset. The user
# writes them on paper (or engraves, or stores in a safe). To recover:
# read the 32 words, paste into --recover, get the unlock back.

import hashlib as _hashlib

# Compact 256-word list (subset of BIP-39 English wordlist, first 256).
# All words are 3-8 chars, no duplicates, alphabetic.
_WORDLIST_256 = (
    "abandon ability able about above absent absorb abstract absurd abuse access "
    "accident account accuse achieve acid acoustic acquire across act action actor "
    "actress actual adapt add addict address adjust admit adult advance advice "
    "aerobic affair afford afraid again age agent agree ahead aim air airport aisle "
    "alarm album alcohol alert alien alley alone alpha already also alter always "
    "amateur amazing among amount amused analyst anchor ancient anger angle angry "
    "animal ankle announce annual another answer antenna antique anxiety any apart "
    "apology appear apple approve april arch arctic area arena argue arm armed "
    "armor army around arrange arrest arrive arrow art artist artwork ask aspect "
    "assault asset assist assume asthma athlete atom attack attend attitude attract "
    "auction audit august aunt author auto autumn average avocado avoid awake "
    "aware away awesome awful awkward axis baby bachelor bacon badge bag balance "
    "balcony ball bamboo banana banner bargain barrel base basic basket battle beach "
    "bean beauty because become beef before begin behave behind believe below belt "
    "bench benefit best betray better between beyond bicycle bid bike bind biology "
    "bird birth bitter black blade blame blanket blast bleak bless blind blood "
    "blossom blouse blue blur blush board boat body boil bomb bone bonus book boost "
    "border boring borrow boss bottom bounce box boy bracket brain brand brass "
    "brave bread breeze brick bridge brief bright bring brisk broccoli broken bronze "
    "broom brother brown brush bubble buddy budget buffalo build bulb bulk bullet "
    "bundle bunker burden burger burst bus business busy butter buyer buzz cable "
    "cactus cage cake call calm camera camp can canal cancel candy cannon canoe "
    "canvas canyon capable capital captain car carbon card cargo carpet carry cart "
    "case cash casino castle casual cat catalog catch category cattle caught cause "
    "caution cave ceiling cement census century cereal certain chair chalk champion "
    "change chaos chapter charge chase chat cheap check cheese chef cherry chest "
    "chicken chief child chimney choice choose chronic chuckle chunk churn cinema "
    "circle citizen civil claim clap clarify claw clay clean clerk clever click "
    "client cliff climb clinic clip clock clog close cloth cloud clown club clump "
    "cluster clutch coach coast coconut code coffee coil coin collect color column "
    "combine come comfort comic common company concert conduct confirm congress "
    "connect consider control convince cook cool copper copy coral core corn "
    "correct cost cotton couch country couple course cousin cover coyote crack "
    "cradle craft cram crane crash crater crawl crayon crazy cream credit creek "
    "crew cricket crime crisp critic crop cross crouch crowd crucial cruel cruise "
    "crumble crunch cry crystal cube culture cup curious current curtain curve "
    "cushion custom cute cycle dad damage damp dance daring dash daughter dawn"
).split()


def _word_to_idx(word: str) -> int:
    w = word.strip().lower()
    try:
        return _WORDLIST_256.index(w)
    except ValueError as exc:
        raise ValueError(f"word not in 256-word list: {word!r}") from exc


def _idx_to_word(idx: int) -> str:
    if not 0 <= idx < 256:
        raise ValueError(f"index out of range: {idx}")
    return _WORDLIST_256[idx]


def _key_to_phrase(key: bytes) -> list[str]:
    """Encode 32-byte key as 32 words from the 256-word list (8 bits each)."""
    if len(key) != 32:
        raise ValueError(f"key must be 32 bytes, got {len(key)}")
    return [_idx_to_word(b) for b in key]


def _phrase_to_key(phrase: list[str]) -> bytes:
    """Decode 32-word phrase back into a 32-byte key."""
    if len(phrase) != 32:
        raise ValueError(f"phrase must be 32 words, got {len(phrase)}")
    return bytes(_word_to_idx(w) for w in phrase)


def cmd_backup(args: argparse.Namespace) -> int:  # type: ignore[name-defined]
    """Print a 32-word paper-backup phrase for the current unlock."""
    if not UNLOCK_PATH.exists():
        print("no unlock at", UNLOCK_PATH, "— run --init first", file=__import__("sys").stderr)
        return 1
    key = UNLOCK_PATH.read_bytes()
    if len(key) == 44:
        # urlsafe-base64 form — decode to raw 32 bytes for phrase
        import base64 as _b64
        key = _b64.urlsafe_b64decode(key)
    phrase = _key_to_phrase(key)
    print()
    print("=" * 70)
    print("  PAPER BACKUP — 32-word recovery phrase for the owner unlock")
    print("=" * 70)
    print()
    print("  Write these words on paper. Store in a safe. NEVER photograph.")
    print("  Anyone with these 32 words can decrypt every state file.")
    print()
    for i in range(0, 32, 4):
        print("    " + "  ".join(f"{j+1:2d}.{w:<10}" for j, w in enumerate(phrase[i:i+4], i)))
    print()
    print("  Verify by running: secure_state.py --verify-phrase '<word1> ... <word32>'")
    print("  Recover by running: secure_state.py --recover '<word1> ... <word32>'")
    print("=" * 70)
    return 0


def cmd_recover(args, words=None) -> int:
    """Restore .owner_unlock from a 32-word phrase."""
    import sys as _sys
    if words is None:
        argv = _sys.argv[1:]
        if "--recover" not in argv:
            print("--recover requires 32 space-separated words", file=_sys.stderr)
            return 1
        idx = argv.index("--recover")
        words = argv[idx + 1:]
    if len(words) != 32:
        print(f"need exactly 32 words, got {len(words)}", file=_sys.stderr)
        return 1
    try:
        key = _phrase_to_key(words)
    except ValueError as exc:
        print(f"invalid phrase: {exc}", file=_sys.stderr)
        return 1
    UNLOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNLOCK_PATH.write_bytes(key)
    os.chmod(UNLOCK_PATH, 0o600)
    print(f"recovered unlock → {UNLOCK_PATH} ({len(key)} bytes, mode 0600)")
    return 0


def cmd_verify_phrase(args, words=None) -> int:
    """Verify that 32 words reconstruct the current unlock."""
    import sys as _sys
    if words is None:
        argv = _sys.argv[1:]
        if "--verify-phrase" not in argv:
            print("--verify-phrase requires 32 space-separated words", file=_sys.stderr)
            return 1
        idx = argv.index("--verify-phrase")
        words = argv[idx + 1:]
    if len(words) != 32:
        print(f"need exactly 32 words, got {len(words)}", file=_sys.stderr)
        return 1
    try:
        key = _phrase_to_key(words)
    except ValueError as exc:
        print(f"invalid phrase: {exc}", file=_sys.stderr)
        return 1
    if not UNLOCK_PATH.exists():
        print("no current unlock to verify against", file=_sys.stderr)
        return 1
    cur = UNLOCK_PATH.read_bytes()
    if len(cur) == 44:
        import base64 as _b64
        cur = _b64.urlsafe_b64decode(cur)
    if _hashlib.sha256(key).digest() == _hashlib.sha256(cur).digest():
        print("phrase matches current unlock ✓")
        return 0
    print("phrase does NOT match current unlock ✗")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
