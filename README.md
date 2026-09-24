# MyHermes Starter

Hermes Agentを本人の複数環境で利用するための公開コンパニオンと共通契約です。アカウントの会社・取引先・個人という区分で、Hermesのプロファイルを分割しません。

環境登録・人格同期、複数アカウントのGitHub接続、会社・個人skillsの配布と同期、OpenRouter relay、本文を収集しないOTel・監査ログを実装しています。導入・固定版への更新、失効、バックアップ・復旧、Ubuntu/macOSの運用検証も進めています。実際のOS・実接続の確認範囲は[OS_CHECKS.md](docs/OS_CHECKS.md)に分けて記録しています。

- 人格同期はrevision、競合、offline outbox、冪等な再送、削除履歴を扱います。
- GitHubは会社・取引先・個人の複数接続を、同じ会話から明示的な接続IDとリポジトリで読み取れます。資格情報は環境ごとのnative secure storeに保持します。
- 会社指定の接続ガイドを取得し、各Hermes環境のMCP接続状態をMyHermesへ報告します。同梱スキルと`myhermes_connections`ツールが未接続時の案内を行います。[接続状態と会社ガイド](docs/CONNECTION_DIRECTORY.md)を参照してください。
- `self-update --check`で会社の指定するCompanion版を確認し、`self-update --apply`でハッシュを検証して更新できます。[更新手順と旧版からの導入](docs/COMPANION_UPDATE.md)を参照してください。
- skillsは参照資料・スクリプト・バイナリを含むpackageを扱い、会社配布と本人用を別の権限で管理します。本人用の自動全社公開は行いません。
- relayは通常応答・SSE・function tools・構造化出力、モデル/プロバイダー許可、利用量の確定と未取得を扱います。監視の受信検証・保存・集計は公開module内で定義します。異常検知や自動処分は実装しません。
- `myhermes start`はDockerによる端末・コード・対応するファイル操作のサンドボックスを既定とし、利用できない場合は起動を止めます。Hermes本体と認証・同期はホストで動きます。[サンドボックスの設定と範囲](docs/SANDBOX.md)を参照してください。
- Macの標準GUIは`myhermes desktop`で起動します。初回に`myhermes prepare-desktop`を実行すると、MyHermesの同期・推論先制限を使うHermes Desktopを準備できます。[GUIの管理範囲](docs/DESKTOP.md)を参照してください。

## 構成

- `src/myhermes/`: Python companion、OS secure store、署名認証、永続outbox、同期・起動処理。
- `packages/core/`: version固定で配布するTypeScript契約、監視検証・保存・集計、本文を保存しないrelay。
- `schemas/`: 公開wire schema。
- `.agents/skills/`: Codexから公開CLIを安全に利用するためのskills。
- `.claude/skills/`: Claude Codeから使うskills。外形ヘルスチェックは本人の依頼文やMemoryにある接続先を使い、会社固有URLをrepoに含めません。
- `hermes-skills/`: 同じHermes会話から接続を使い、本人用skillsを作成・更新する公開支援skills。Python wheelにも同梱します。
- `tests/`: 架空データによるクライアント受入テスト。

## ローカル開発

Node.js 24以上、Python 3.11以上、Gitを使います。コンパニオンはPython 3.14でも実行できます。固定しているHermes本体は現時点で3.14に対応していないため、Hermes runtime用にPython 3.11〜3.13も用意してください。MacでPython 3.14が標準の場合、Homebrewなら`brew install python@3.13`で併設できます。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
npm ci
npm run check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check src tests
```

最初のコマンドはコンパニオン用venvを`python3`で作成します。固定Hermes本体の導入・更新には、別途`python3.13`など3.11〜3.13の実行ファイルを`install-runtime --python`または`upgrade --python`へ渡してください。Public単独でのテストは会社のPrivate repoや実アカウントを必要としません。Ubuntu/macOSのCI実行結果は[GitHub Actions](https://github.com/nyasukun/myhermes-starter/actions)で確認できます。

## 利用を始める

Claude Code・Codexから本人のHermesを呼び出す場合は、[MyHermes MCP連携](docs/HERMES_MCP.md)を参照してください。`myhermes-mcp`は既存の環境認証・同期・Docker設定を使うstdioサーバーです。

初回導入、Dockerサンドボックスでの日常利用、資料の受け渡し、ポータル、更新、2台目の登録までの手順は[利用者ガイド](docs/USER_GUIDE.md)にあります。通常の`myhermes start`はDockerを使い、利用できない場合は起動を止めます。

本人認証済みのMyHermesサーバーを用意した後、所有者自身の端末で設定・登録します。URLやパスは利用する環境に置き換えてください。以下の例に資格情報はありません。

```sh
.venv/bin/myhermes setup --server https://myhermes.example.com \
  --hermes-home "$HOME/.hermes" --upstream "$HOME/.local/share/myhermes/hermes-agent" \
  --label "My environment" --dry-run
```

結果を確認して`--dry-run`を外して設定します。`enroll`は**所有者のネイティブ端末**で実行してください。Codexが捕捉するPTYで実行しないでください。ブラウザで本人認証し、端末に表示された一度限りのコードで承認します。秘密鍵は対応するOS credential storeへ保存し、平文ファイルへ自動で代替保存しません。

`myhermes skills bootstrap`で、wheel同梱の`myhermes-skills`と`myhermes-connections`を設定済みHermes homeへ配置できます。通常のmanaged startでも起動前に確認します。これらはアカウント別プロファイルを作らず、会社・個人skillsと同じ会話で使います。予約済みの同名ディレクトリに変更がある場合は停止し、明示的な`skills bootstrap --restore`で退避してから復元します。[配布物の確認](docs/DISTRIBUTION.md)を参照してください。

`myhermes sync`は許可された3ファイルだけを同期します。履歴・競合の本文は標準出力へ出さず、ポータルまたは明示的なローカルexportで確認します。実行中Hermesへの同期はmanaged-session lockで防ぎます。別途直接起動したHermesまで制御する保証はありません。

コマンドと復旧は[COMPANION.md](docs/COMPANION.md)、接続は[CONNECTIONS.md](docs/CONNECTIONS.md)、skillsは[SKILL_SYNC.md](docs/SKILL_SYNC.md)、relayは[RELAY_BRIDGE.md](docs/RELAY_BRIDGE.md)、監視は[MONITORING.md](docs/MONITORING.md)を参照してください。[API.md](docs/API.md)が公開契約の入口です。

## 採用upstreamと公開境界

公式Hermes Agent `v2026.9.7` / `2237be355906fbe6065ce1815711eee52b2d646e`を採用します。[互換性調査](docs/UPSTREAM_COMPATIBILITY.md)に実パス、形式、読み込みタイミング、上限、ライセンスと固定sourceを記録しています。公式checkoutは変更せず、Desktopだけは同じcommitのコピーへ公開コンパニオンの管理用アダプターを適用します。

人格同期は`SOUL.md`、`memories/MEMORY.md`、`memories/USER.md`のみ。本人用skill packageは別のAPI・outboxで明示的に登録・同期します。セッションDB、会話、資格情報、cache、任意の作業文書、home全体をアップロードしません。[データの取扱い](docs/PRIVACY.md)も確認してください。

このrepoと配布物には、会社固有設定、実ユーザーの本文、実ログ、秘密情報を入れません。`npm run pack:core`で生成したmoduleはPrivateアプリが固定した内容で利用します。会社固有のUI・権限・設定・配備はPrivateアプリが担当し、Private側だけで監視項目を増やす構成にはしません。
