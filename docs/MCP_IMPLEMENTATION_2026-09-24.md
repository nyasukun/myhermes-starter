# MyHermes MCP 実装記録 — 2026-09-24 JST

この文書はローカル実装完了時点の記録です。その後のcommit・pushはGit履歴で確認してください。

## 対象と依頼範囲

アカウント別の連携ガイド作成と、Claude Code・CodexからHermesを呼ぶMCPの不足実装。呼び出し方向はユーザーの回答により確定した。

## 変更

- Public companionに`myhermes-mcp`を追加。stdio MCPの`hermes_status`と`hermes_ask`を公開する。
- `cli.execute`に内部runtime launcher引数を追加し、既存の本人認証・home/runtime排他・前後同期・metadata監視を共用する。
- 別processで起動処理をmain threadに置き、stdinの依頼をHermes quiet oneshotへ渡す。親クライアントの資格情報環境を除外し、stdoutを制限、stderrを破棄する。
- Claude Code用JSONとCodex用TOMLを新規ファイルへ生成する。ユーザーの稼働中アプリ設定を変更しない。
- optional dependency `mcp==1.26.0`、console script、CI extra、専用テスト、利用ガイドを追加。
- 同じ作業ツリーに存在したDesktop関連の変更は保持した。今回の変更対象として扱わない。

公開HTTP wire contract、Core packageの版、persona allowlist、Cloudflare設定には変更なし。配布版のversionはローカル既存の0.3.0を保持し、公開リリースは行っていない。

## 手段と検証

ローカルファイル操作、Python/pip、npm、Ruff、公式文書・公式固定sourceの読み取りを使用。SDK依存導入、localhostの合成API、合成子processの停止確認は許可されたsandbox escalationで実行した。検証結果と再現手順は[HERMES_MCP.md](HERMES_MCP.md)に記録した。実ユーザーの本文・実ログ・認証値をこの記録に含めない。

## 残作業

- 利用する端末の登録済みMyHermes state directoryを指定して、本人のClaude Code・CodexへMCPを登録する。
- 許可した実モデルで固定HermesとのE2Eを行う。
- Gmail/Drive/Notion/SlackのOS secure store対応と、Paperclipのsecure-store launcherは別のサービス側connector実装として残る。
- 実アカウントのOAuth/PAT認可は本人端末で行う。push・release・本番配備は未実施。

Cloudflare MCP/API/CLIのリソース操作、Computer Use、外部へのメッセージ送信は行っていない。
