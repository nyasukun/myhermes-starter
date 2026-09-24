# Claude Code・CodexからMyHermesを呼ぶ

## 受入条件（実装前に確定）

- 連携方向は **Claude Code / Codex → stdio MCP → MyHermes → Hermes**。
- `hermes_status`はローカル設定のメタデータだけを返し、モデルを呼ばない。
- `hermes_ask`は本人が固定したstate directoryで、既存のmanaged startと同じ本人確認・排他・開始前同期・終了後同期を使う。アカウントごとのHermes homeを作らない。
- 依頼文はstdinだけで渡す。資格情報、SOUL、memory、会話全体を自動で呼び出し元へ返さない。MCPには最終回答を返し、監視には既存契約のメタデータだけを送る。
- 読み書きやサービス利用には、Hermesに設定された既存の権限とDocker実行設定を使う。対話TTYや認証の省略、sandboxの迂回を追加しない。
- 依頼文・応答サイズ、実行時間・ターン数を制限する。中断・タイムアウトで子プロセスを回収し、可能な終了後同期を実行する。同じhomeの同時起動は既存lockで拒否する。
- 設定フラグメントを生成し、既存のClaude/Codex/Hermes設定を上書きしない。
- 合成データでSDK stdio・ライフサイクル・失敗経路を検証する。実アカウント、実モデル、Ubuntu、本番配備は区別する。

## 接続の構成

```text
Claude Code ── MCP stdio ──┐
                          ├─ myhermes-mcp → 本人のMyHermes環境 → Hermes
Codex       ── MCP stdio ──┘                 同期・relay・Docker
```

両クライアントは同じ`state-dir`を指定します。会話ごとに新しいHermesセッションを起動し、既存のSOUL・メモリ・skillsを利用します。Claude/Codex側の会話履歴全体や、その認証情報は渡しません。直前の依頼の続きを扱わせる場合は、`prompt`に必要な要点を含めてください。

通常の`myhermes start`またはDesktopが同じhomeを使用中なら、MCPからの起動は失敗します。開いているHermesを終了してから再実行します。二つのクライアントで同時に`hermes_ask`した場合も、同じ排他が働きます。

MyHermesサーバーの公開HTTP API、監視項目、同期ファイルのallowlistは変更していません。stdioは同じ端末のプロセス間接続で、公開ポートやCloudflareの変更は不要です。

## 1. 導入

登録済みのcompanion環境へ、今回のソースとMCP追加依存を導入します。開発checkoutから使う例です。

```sh
cd /absolute/path/to/myhermes-starter
.venv/bin/python -m pip install -e '.[mcp]'
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes" inspect
```

`state-dir`が標準以外なら実際の設定に置き換えます。`setup`で別homeを新規作成し直す必要はありません。未登録の場合は[利用者ガイド](USER_GUIDE.md)で本人の端末から登録してください。macOSはKeychain、Ubuntuは本人のSecret Serviceセッションへアクセスでき、Dockerが利用できる状態にします。

以下の`/absolute/path/to/myhermes-starter/.venv/bin/python`は、この追加依存を導入したPythonの絶対パスです。Hermes本体用venvのPythonとは異なります。仮想環境を移動するとクライアント設定も更新が必要です。

## 2. Claude Codeへ追加

本人の端末で実行します。`--scope user`はそのOSユーザーのClaude Code用設定です。

```sh
claude mcp add --transport stdio --scope user myhermes -- \
  /absolute/path/to/myhermes-starter/.venv/bin/python \
  -m myhermes.hermes_mcp serve \
  --state-dir "$HOME/.local/state/myhermes"
```

同名が既にあるときは追加を繰り返さず、`claude mcp get myhermes`で対象を確認します。長めのタスクを扱う例として、本人の端末から次のようにClaude Codeを起動し、`/mcp`で`myhermes`を確認してください。

```sh
MCP_TOOL_TIMEOUT=480000 claude
```

フラグメントを使う場合は次を実行します。**出力先は新規ファイル**にしてください。

```sh
.venv/bin/myhermes-mcp config --client claude-code \
  --state-dir "$HOME/.local/state/myhermes" \
  --output /private/local/path/myhermes-claude.json
```

これは`mcpServers.myhermes`を含むJSONです。`--mcp-config /private/local/path/myhermes-claude.json`を使うか、本人管理の既存JSONへ該当エントリを統合します。設定ファイル全体を置き換えないでください。`--scope project`やrepo内`.mcp.json`へ個人の端末パスを公開する必要はありません。

Claude CodeはMCPクライアントとして接続します。`claude mcp serve`を起動する手順ではありません。[Claude Code公式MCPリファレンス](https://code.claude.com/docs/en/mcp)

## 3. Codexへ追加

本人の端末で実行します。

```sh
codex mcp add myhermes -- \
  /absolute/path/to/myhermes-starter/.venv/bin/python \
  -m myhermes.hermes_mcp serve \
  --state-dir "$HOME/.local/state/myhermes"
```

通常は`~/.codex/config.toml`に登録されます。その`[mcp_servers.myhermes]`へ、次のタイムアウトを設定します。同じテーブルを二重に追加しないでください。

```toml
startup_timeout_sec = 30
tool_timeout_sec = 480
```

完全なエントリを新規フラグメントへ生成することもできます。

```sh
.venv/bin/myhermes-mcp config --client codex \
  --state-dir "$HOME/.local/state/myhermes" \
  --output /private/local/path/myhermes-codex.toml
```

生成した`[mcp_servers.myhermes]`を既存設定へ統合して、`codex mcp list`とCodex内の`/mcp`で確認します。同じホストのCodexクライアントはこの設定を共有します。既に開いているタスクでツールが見えなければ、MCP再接続または新しいタスクを利用してください。[OpenAI公式MCP設定](https://developers.openai.com/codex/mcp)

Codexが外部MCPへ接続する方式です。旧`codex mcp-server`の提供状況には依存しません。

## 4. 動作確認

まず各クライアントから次のように依頼します。

> myhermesのhermes_statusを呼んで、環境が登録済みか確認して。

`enrolled: true`はローカルの登録記録です。サーバー側の失効、Dockerの動作、モデル利用可能性を検証した表示ではありません。

続いて、モデルを利用してよいタイミングで次の依頼をします。

> myhermesのhermes_askを使って、Hermesに「接続確認です。短く応答してください。外部サービスの参照や変更、メモリの更新は不要です」と伝えて。

`status: completed`と`answer`を確認します。これは実モデル利用になるため、通常のMyHermes relayの利用量に含まれます。読み取りだけの`hermes_status`とは区別してください。

アカウントを用いる依頼では、本文に接続名・範囲を明示します。

> Hermesに、gmail_work_meだけで指定した期間のメールを確認し、要点を返してもらって。送信やラベル変更はしないで。

これは該当接続がHermes側で認可・導入済みの場合に使える依頼例です。MCPサーバーの追加だけではGmail等のアカウントは接続されません。

## 実行範囲と保存

- `hermes_ask(prompt)`はHermesの既存ツールを使用でき、メモリや外部サービスを変更し得ます。MCP上でも読み取り専用とは宣言していません。呼び出し元で、依頼した作業に応じて承認してください。
- 既定値は12ターン、Hermes実行予算300秒です。起動時の`--max-turns`と`--run-budget`で変更できます。MCP toolの引数からは変更できません。
- 入力上限32 KiB、各子プロセスのstdout上限1 MiBです。上限を超える結果は成功として途中まで返しません。Hermes起動には予算+15秒、同期を含むworkerには予算+120秒の上限を設けます。中断後のworker終了には最大45秒の猶予を置きます。
- 依頼文はstdinで渡し、子プロセスのstderrは保存・返却しません。最終回答は呼び出し元のClaude Code/Codex会話へ渡るため、そのクライアントの履歴対象になります。
- Hermes本体は通常のセッション保存・メモリ動作を維持します。MCPサーバーは独自の会話アーカイブを追加しません。SOUL等の通常同期と本文を含まない監視の境界も維持します。
- 終了後同期に失敗すると、回答があっても`status: incomplete`になる場合があります。`synchronization`を確認し、本人の端末で`myhermes sync`を実行して復旧します。
- HermesのDocker内`/workspace`と、Claude Code/Codexのホスト側プロジェクトは別の場所です。作業ファイルは[サンドボックスの資料受け渡し](SANDBOX.md)に沿って渡してください。
- Hermes自身の`mcp_servers`へこの`myhermes`サーバーを登録しないでください。再帰呼び出しとhomeの排他競合になります。

## 解除・更新

`claude mcp remove --scope user myhermes`、`codex mcp remove myhermes`で各クライアントから解除できます。Hermesの環境登録・人格・外部サービスの認可は削除されません。サーバーの停止だけではサービス側の認可取消にはなりません。

コード更新後はcompanionへ同じ`[mcp]` extraを入れ直し、両クライアントのMCPプロセスを再起動してください。この変更はローカル実装で、公開配布版へリリースした記録ではありません。

## 今回の検証結果（2026-09-24）

macOS / Python 3.11のローカル検証です。

| 検証 | 結果 |
| --- | --- |
| 最終MCP専用テスト | 9件成功。実SDKのstdio初期化・tools/list・呼び出し、引数制限、失敗の非露出、サイズ超過、中断・timeout時の子process回収、既存companionを使った前後同期・排他・回答の監視非混入、ACK未送達のincomplete表示 |
| 既存Python全体（専用テスト7件を含む時点） | 351件実行、334件成功、17件skip。skipはnative credential store・明示した固定runtime・Docker等のopt-in検証 |
| 公開Core | 型・schemaチェックと70テスト成功 |
| コード品質・配布 | Ruff check成功。変更Pythonのformat成功。wheel内3モジュールのsource一致、MCP追加依存・console entrypoint、ガイド内ローカルリンク、git diff --checkを確認 |

専用テストのHermes実行ファイル、persona API、relay上流は合成fixtureです。既存companionのライフサイクルとMCP SDKは本物を使いました。固定公式Hermesの`--query-file -`、`--oneshot`、`-Q`の挙動は固定sourceで確認しましたが、今回そのruntimeを使う実モデルE2Eは実施していません。Ubuntuでの今回の変更の実行、実Keychain/Secret Service認可、実アカウント・実サービスへの操作、公開リリースと本番配備は未実施です。CIは`.[dev,mcp]`を導入し、このMCPテストも対象にします。
