# キャンセル空き監視システム（引き継ぎ説明書）

カービィカフェ／JAL工場見学／ANA工場見学の**予約キャンセル空きを自動で見張り、空きが出たらLINEに通知**するシステム。オーナーは非エンジニア。この文書は、別のエンジニアやAIエージェント（Codex等）が作業を引き継ぐための説明書。

> **最重要:** この監視は **GitHub Actions 上で自律的に動いている**。ClaudeやCodexなどのAIは「設定を変える／直す作業員」にすぎず、監視の稼働には関与しない。**AIを乗り換えても監視は止まらない。**

---

## 1. いま何が動いているか（2026-07 時点）

| 監視 | 本番ワークフロー | 状態 | 現在の設定 |
|---|---|---|---|
| カービィカフェ TOKYO（旧7月ページ） | `monitor.yml` | ⏹️ 停止 | 2名・7/16〜19（全時間帯） |
| カービィカフェ TOKYO（新予約サイト） | `kirby-august-monitor.yml` | ✅ 稼働 | 4名・2026年8月14日〜15日（全時間帯） |
| JAL工場見学 SKY MUSEUM | `jal-monitor.yml` | ✅ 稼働 | 4名・工場見学コース・日本時間のちょうど30日後は除外 |
| ANA工場見学 | `ana-monitor.yml` | ⏹️ 停止（**意図的**・触らない） | 4名 |

設定変更はよく来る（人数・日程・時間帯・追加/削除・停止/再開）。オーナーは気軽に付け外しする。

---

## 2. 仕組みの全体像

```
GitHub Actions（cronで1日4回、各ジョブが6時間弱ループ）
   └─ 2〜3分おきに Python を実行
        └─ 予約サイトをチェックして「予約可能な枠」を探す
             └─ 新しい空きが見つかったら LINE Messaging API で通知
```

- **なぜループ型か:** GitHubのcronは遅延が激しく「5分おき」等が当てにならない。そこで「6時間動くジョブを1日4回起動し、ジョブ内部で `sleep` して正確な間隔を作る」方式にしている。
- **通知先:** LINE公式アカウント「USJ監視ボット」。トークン類は GitHub Secrets（`LINE_CHANNEL_ACCESS_TOKEN`, `LINE_USER_ID`）に暗号化保存。**コードや履歴には平文で置かない。**
- **リポジトリはPublic運用:** privateだとActions無料枠（月約2000分）をループ型が1〜2日で使い切り、ジョブが「枠切れ」で即失敗する。オーナー承認の上でPublicにして無制限化した。機密はSecretsのみでコードに無いことを確認済み。

---

## 3. ファイル構成

| ファイル | 役割 |
|---|---|
| `monitor.py` | カービィカフェ本体。先頭の `TARGETS`（配列）に監視条件を書く。複数並行可。 |
| `kirby_august_monitor.py` | 新予約サイトの公開カレンダーを使う2026年8月専用監視。TOKYO・4名・8/14〜15・全時間帯。予約操作はしない。 |
| `jal_monitor.py` | JAL工場見学本体。先頭の `PEOPLE` / `COURSE_KEYWORD` / `MONTHS_AHEAD` で設定。 |
| `ana_monitor.py` | ANA工場見学本体。先頭の `PEOPLE` / `MONTHS_AHEAD` で設定。 |
| `.github/workflows/monitor.yml` | カービィ本番（cron＋内部ループ）。`sleep` の秒数がチェック間隔。 |
| `.github/workflows/test-once.yml` | カービィを1回だけ実行する検証用（手動起動）。 |
| `.github/workflows/kirby-august-monitor.yml` / `kirby-august-test.yml` | 新予約サイトの8月監視 本番／検証。 |
| `.github/workflows/jal-monitor.yml` / `jal-test.yml` | JAL 本番／検証。 |
| `.github/workflows/ana-monitor.yml` / `ana-test.yml` | ANA 本番／検証。 |
| `notified_slots.txt` | カービィの通知済み枠（累積）。重複通知を防ぐ記録。 |
| `notified_slots_kirby_august.txt` / `kirby_august_health.txt` | 新予約サイト8月監視の通知済み枠／自己点検状態。 |
| `kirby_auto_book.py` | カービィ7月分の自動予約フォーム入力・確定処理。 |
| `auto_book_status.txt` | 自動予約の成功／安全停止状態。個人情報や予約番号は置かない。 |
| `notified_slots_jal.txt` / `notified_slots_ana.txt` | JAL／ANAの通知済み枠。 |
| `jal_health.txt` / `ana_health.txt` | 自己点検の状態（"ok" / "ng:日付"）。 |

---

## 4. よくある変更のやり方

### カービィカフェ（`monitor.py` の `TARGETS`）

`TARGETS` は監視条件の配列。1件が1つの `{...}`。複数書けば並行監視。

```python
TARGETS = [
    {
        "name": "TOKYO 2名 7/16-19",       # 通知に出る名前。人数と日程を入れると分かりやすい
        "reserve_url": "https://kirbycafe-reserve.com/guest/tokyo/reserve/",
        "booking_url": "https://kirbycafe-reserve.com/guest/tokyo/",
        "people": 2,                        # 人数
        "date_from": date(2026, 7, 16),     # 監視開始日
        "date_to":   date(2026, 7, 19),     # 監視終了日
        # 時間帯を絞る場合のみ time_filter を書く（無ければ全時間帯）
        # "time_filter": {
        #     28: (17 * 60, 24 * 60),   # 28日は17:00以降
        #     29: (0, 14 * 60),         # 29日は14:00まで
        # },
    },
    # 2件目以降を並行監視したければ {...} を追加
]
```

- **人数変更:** `people` を変える。`name` も合わせて直す（通知の分かりやすさのため）。
- **日程変更:** `date_from` / `date_to`。
- **時間帯を絞る:** `time_filter` を書く。キーは「日」、値は `(開始分, 終了分)`。分単位＝`時*60`。例: 17:00以降=`(17*60, 24*60)`、14:00まで=`(0, 14*60)`。
- **追加:** `{...}` をもう1つ足す。
- **削除/解除:** その `{...}` を消す。

**変更したら `notified_slots.txt` を空にリセット**（`: > notified_slots.txt`）。人数や日程が変わると通知済み記録の意味が変わるため。

### カービィ7月分の自動予約

`TARGETS` で `"auto_book_july_2026": True` の対象は、設定が揃った場合だけ、最初に見つけた日時の早い空き1枠を同じブラウザで確保し予約確定まで進める。**2026年7月以外はコード側でも拒否**する。

個人情報はPublicリポジトリに書かず、以下の GitHub Actions Secrets に保存する。

- `KIRBY_NAME_LAST` / `KIRBY_NAME_FIRST`: 姓／名
- `KIRBY_KANA_LAST` / `KIRBY_KANA_FIRST`: セイ／メイ
- `KIRBY_MOBILE`: 電話番号（数字のみ）
- `KIRBY_MOBILE_FALLBACK`: 最初の電話番号がフォーム検証で弾かれた場合だけ使う予備番号（任意）
- `KIRBY_EMAIL`: 予約確認メールアドレス

以下は GitHub Actions Variables。すべて揃うまで自動予約は無効。

- `KIRBY_PRIVACY_CONSENT=YES`: 予約画面の個人情報取扱いを本人が確認・同意済み
- `KIRBY_AUTO_BOOK_ENABLED=true`: 最後に設定する有効化スイッチ

安全策:

- バースデーサービスは「希望しない」、案内メール受信はオフ、その他要望は空欄で予約する。
- 予約成功時は `auto_book_status.txt` に `BOOKED` を記録し、同じ対象の監視・追加予約を止める。
- 枠確保後にフォーム処理が失敗した場合は、可能なら枠を解放し、`DISABLED` を記録して自動予約を安全停止する。
- 予約成功または安全停止時は `monitor.yml` 自体も無効化し、状態ファイルのpushに失敗しても再試行し続けない。
- `test-once.yml` は常に `KIRBY_AUTO_BOOK_ENABLED=false` で、単発テストが実在枠を確保しない。
- 本番は concurrency group で多重実行を防ぐ。

### カービィ新予約サイト・2026年8月

`kirby_august_monitor.py` はログイン不要の「予約空き状況のご案内（確認のみ）」で、人数を先に4名、店舗をTOKYOの順に選び、2026年8月へ移動して8月14日〜15日の全時間帯を確認する。公開カレンダー上の `○` だけを空きとしてLINE通知する。セルや予約ボタンはクリックしない。

設定を変更する場合は、冒頭の `STORE` / `PEOPLE` / `TARGET_YEAR` / `TARGET_MONTH` / `TARGET_DAYS` を変更し、`notified_slots_kirby_august.txt` を空にする。対象月を変えるときはファイル名・通知文・ワークフロー名も整理する。

### JAL（`jal_monitor.py` 冒頭）
`PEOPLE`（人数）、`COURSE_KEYWORD`（コース名の部分一致。`""`で全コース）、`MONTHS_AHEAD`。`EXCLUDE_DAYS_AHEAD=30` により、日本時間の今日からちょうど30日後（予約開始直後の新規公開枠）は通知しない。翌日になって同じ日が29日後になれば、空きが続いている場合は通常の通知対象になる。人数・コース等を変更した場合は `notified_slots_jal.txt` を空にリセットする。

### ANA（`ana_monitor.py` 冒頭）
`PEOPLE`、`MONTHS_AHEAD`。変更後は `notified_slots_ana.txt` を空にリセット。

---

## 5. 反映の手順（毎回これ）

`gh` CLI が使える前提（GitHub操作）。`SLUG = satoru12h-hub/kirby-cancel-monitor`。

```bash
# 0) 最新を取得
git clone https://github.com/satoru12h-hub/kirby-cancel-monitor.git && cd kirby-cancel-monitor

# 1) 該当スクリプトを編集（例: monitor.py の TARGETS）
#    必要なら記録をリセット: : > notified_slots.txt

# 2) 構文チェック
python3 -c "import ast; ast.parse(open('monitor.py').read()); print('OK')"

# 3) 動いている本番ジョブを止める（カービィの例）
gh run list --workflow=monitor.yml --status in_progress --json databaseId \
  --jq '.[].databaseId' | xargs -r -I{} gh run cancel {}

# 4) commit & push
git add -A && git commit -m "設定変更..." && git push

# 5) 検証（1回だけ実行して結果を見る）
gh workflow run test-once.yml
#   完了後: gh run view --job=<id> --log で「◯名を選択」「◯月をスキャン」「結果」を確認

# 6) 本番を起動
gh workflow run monitor.yml
```

JAL/ANAは `monitor.yml`→`jal-monitor.yml`/`ana-monitor.yml`、`test-once.yml`→`jal-test.yml`/`ana-test.yml` に読み替え。

新予約サイトの8月監視は `kirby-august-monitor.yml` / `kirby-august-test.yml` を使う。旧7月用の `monitor.yml` は停止状態を維持する。

### 停止 / 再開
```bash
# 停止（今後の自動起動も止める）
gh workflow disable jal-monitor.yml
gh run list --workflow=jal-monitor.yml --status in_progress --json databaseId --jq '.[].databaseId' | xargs -r -I{} gh run cancel {}

# 再開
gh workflow enable jal-monitor.yml
gh workflow run jal-monitor.yml
```

---

## 6. ハマりどころ（重要・過去に踏んだ地雷）

- **カービィの空き判定:** 予約カレンダーのセルは `○`=空き（クリック可能な`<a>`付き）、`×`=満席、**空文字=対象外/過去**。「×以外」で拾うと過去セルを誤検出する。**必ず `○` で判定**すること。
- **カービィの人数選択:** Vuetify の `v-select`（`.v-menu__content` にメニューが出る特殊なドロップダウン）。普通の `<select>` ではないので `select_option` では動かない。ドロップダウンを開いて「◯名様」を選ぶ実装になっている。
- **新予約サイトの公開カレンダー:** `#NumberOfCustomers` と `#StoreSelection` はnative `<select>`。店舗変更時にその時点の人数でデータを取得するため、**人数→店舗の順**に選ぶ。旧ページとは操作方法が異なる。
- **新予約サイトの空き判定:** 指定人数で空き数0は `×`、1以上はクリック可能な `○`、過去・対象外は空文字または `-`。ここでも**必ず `○` だけ**を拾う。
- **カレンダーの月:** 初期表示が翌月になっていることがある。**スキャン前に表示中の年月を検証**し、違えば前月/次月ボタン（`chevron_left`/`chevron_right`）で移動している。誤って別月を読むと誤報になる。
- **重複通知:** 通知済み枠を `notified_slots.txt` に**累積**（消さない・上書きしない）してコミット。これでジョブ交代後も維持され、同じ枠を二度通知しない。過去に「上書き方式」で、空きが一瞬消えて復活するたびに再通知してしまう不具合があった。
- **無料枠:** private のままだとActions枠を食い潰してジョブが2秒で失敗する。**Public維持**が前提。エラーメール「recent account payments have failed...」が来たらこれ。
- **JAL:** 空き状況ページはパラメータ付きURLに直接アクセスするだけで取得可能（同意画面・フォーム不要）。`numberOfPeople` を渡すので、人数に満たない枠は自動で「予約可能数不足」表示になり拾わない。日本時間基準でちょうど30日後の枠は、予約開始直後の通知を避けるため除外する。自己点検（カレンダー表が1つも取れない=異常）でLINEアラートを出す仕組みあり。

---

## 7. トラブル時の見かた

```bash
# 直近の実行一覧（成功/失敗）
gh run list --workflow=monitor.yml --limit 8

# 失敗の中身
gh run view <run-id> --log-failed

# 実際にスキャンが回っているか（完了済みジョブのログ末尾）
gh run view --job=<job-id> --log | grep -E "スキャン中|結果|変化" | tail
```

- ジョブが `in_progress` で継続＝正常（ループ中はログが取れないのは仕様）。
- 全ジョブが数秒で `failure` ＝ 無料枠切れ（Public化を確認）。
- `結果: []` が続く＝プログラムは正常、単に本当に満席。

---

## 8. 連絡事項

- LINE通知が来すぎる/来ない等の不具合は、まず `notified_slots*.txt` の累積ロジックと該当スクリプトの判定条件を疑う。
- 予約サイトのHTML構造が変わると空き判定が壊れる（カフェ側の仕様変更）。その場合は実際のページを開いてセルの構造（`○`/`×`の入り方、ドロップダウンの型）を再確認して合わせる。
- **ANAの本番停止は意図的**。オーナーの指示なく再開しないこと。
