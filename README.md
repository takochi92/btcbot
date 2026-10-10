# btcbot

BTC のトレンドフォロー（移動平均より上なら持つ、下なら現金）を、**仮想売買だけ**で試すボットです。
本物のお金と取引所の鍵は使いません。

- **画面（ライブの仮想売買デスク）: https://takochi92.github.io/btcbot/**　デモ: https://takochi92.github.io/btcbot/?sim=1
- 設計と考え方: [docs/DESIGN.md](docs/DESIGN.md)
- バックテスト結果: [reports/backtest.md](reports/backtest.md)（GitHub Actions が作ります）
- 仮想売買の状態: `state/paper_state.json`、判断の記録: `state/decisions.jsonl`

## 自動で動くもの（GitHub Actions）

| 名前 | いつ | 何をする |
|---|---|---|
| tests | push のたび | テスト |
| backtest | プログラムや設定を変えたとき・手動 | データを取ってバックテストし、結果を保存 |
| publish-page | web/ を変えたとき | 画面を GitHub Pages に出す |
| paper-trade | 毎日 09:17（日本時間）・手動 | バックテストに合格していれば仮想売買を1回進める |

止めたいとき: Actions → paper-trade → Run workflow で kill を `true` にして実行。

## 手元で動かす

```
pip install -r requirements.txt
python -m pytest -q
python -m btcbot backtest     # データを取ってバックテスト
python -m btcbot paper        # 仮想売買を1回
python -m btcbot status       # 今の状態
python -m btcbot kill         # 全部手じまいして止める
python -m btcbot reset        # 止まったのを解除（理由を確かめてから）
```

## 大事な約束

- `config.toml` の [hypothesis] と [cv] を変えるとバックテストが止まります。結果を見てから条件をいじるのを防ぐためです。
  変えるなら新しい仮説として `python -m btcbot lock` で固定し直してください。
- 安全装置（`btcbot/risk.py`）は、設定で上限より緩くできません。
- 本番のお金で動かす機能はありません。
