import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
LOCK_PATH = ROOT / "hypothesis.lock"


class HypothesisChanged(RuntimeError):
    pass


def load(path=CONFIG_PATH):
    return tomllib.loads(Path(path).read_text("utf-8"))


def hypothesis_hash(cfg):
    blob = json.dumps({"hypothesis": cfg["hypothesis"], "cv": cfg["cv"]}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def check_lock(cfg, lock_path=LOCK_PATH):
    """仮説と検証方法が、データを見る前に固定したものと同じか確かめる。"""
    if not Path(lock_path).exists():
        raise HypothesisChanged("hypothesis.lock がありません。データを見る前に `python -m btcbot lock` で固定してください。")
    locked = Path(lock_path).read_text("utf-8").strip()
    if locked != hypothesis_hash(cfg):
        raise HypothesisChanged(
            "config.toml の [hypothesis]/[cv] が固定時から変わっています。"
            "変えるなら新しい仮説として lock し直し、変種の数は結果に正直に数えてください。"
        )
    return locked


def write_lock(cfg, lock_path=LOCK_PATH):
    h = hypothesis_hash(cfg)
    Path(lock_path).write_text(h + "\n", "utf-8")
    return h
