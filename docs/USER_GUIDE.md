# MyHermesを使い始める

MyHermesは、同じ本人の人格・永続メモリ・本人用skillsを複数のHermes環境で共有します。会社、取引先、個人のアカウントも同じHermesから使います。アカウントの種類ごとに環境を分ける必要はありません。

この手順には、会社が用意したMyHermesのURLと、利用を許可された本人アカウントが必要です。サーバーの準備は管理者が行います。公開サンプルのURLには接続できません。

## 1. コンパニオンを導入する

UbuntuまたはmacOSで、管理者から案内されたこの公開リポジトリのレビュー済み版を開きます。GitとPython 3.11〜3.13が必要です。以下はPython 3.11の例です。

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/myhermes --version
```

まだ公開releaseがない開発版を、PyPIから取得できるものとして扱わないでください。配布wheelを受け取った場合は、同じ仮想環境にその検証済みwheelをインストールします。OSの実測範囲と必要な安全な保管機能は[OS_CHECKS.md](OS_CHECKS.md)、配布物の内容は[DISTRIBUTION.md](DISTRIBUTION.md)にあります。

Codexに導入支援を依頼する場合は、このrepoを作業場所にして`setup-myhermes` skillを指定できます。CLIがまだない場合は、この導入手順から始めます。Codex用skillsと、Hermesの会話で使うskillsは別のものです。

## 2. この環境を設定する

次のURLを管理者から指定されたURLに置き換えます。既存のHermes homeを消したり、別の環境からコピーした状態DBを指定したりしないでください。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" setup \
  --server https://myhermes.example.org \
  --hermes-home "$HOME/.local/share/myhermes-work/home" \
  --upstream "$HOME/.local/share/myhermes-work/runtime" \
  --label 'My work environment' --dry-run
```

表示されたOSと同期対象を確認し、同じコマンドから`--dry-run`を外して実行します。続けて、対応する公式Hermesの固定版を導入します。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" install-runtime --python python3.11
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" inspect
```

`install-runtime`はGitとPythonパッケージの取得を行います。成功時は対応版を検証します。既存の変更を強制的に破棄して更新する機能ではありません。

## 3. 本人として環境を登録する

次のコマンドは、**本人が自分のネイティブ端末で実行**します。Codexに捕捉されるツールやPTYでは実行しません。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" enroll
```

端末に表示された一度限りのコードを、開いた会社の認証済みブラウザ画面に入力します。環境名とOSを確認して承認してください。コードやキーをCodexの会話へ貼る必要はありません。

秘密鍵はmacOS KeychainまたはUbuntu Secret Serviceに保存されます。Ubuntuのヘッドレス環境などで安全な保管機能が使えない場合は停止します。平文保存へ切り替えて登録を続けることはありません。

ブラウザ承認後に端末が待機を終えていた場合は、同じ`enroll`を再実行します。同じ登録要求の完了を確認します。`inspect`がinstallation IDを表示することを確認してください。`--dry-run`やブラウザの承認待ちは登録完了ではありません。

## 4. 同期してHermesを起動する

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" sync
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" skills sync
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" start --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" start
```

最後のコマンドは本人の端末で対話セッションを起動します。会社のOpenRouterキーをHermesへ設定する必要はありません。登録環境の認証を使って、会社が許可するrelayとモデルに接続します。

セッション終了時にも人格とskillsを同期します。終了後の通信が失敗した場合は、変更と送信待ちの要求を端末に保持します。`inspect`で状態を確認し、接続回復後に`sync`と`skills sync`を再実行してください。Hermesが終了できたことと、その後の同期が完了したことは別々に結果へ表示されます。[終了時の同期](SESSION_SYNC.md)に終了コードと中断時の扱いがあります。

人格は`SOUL.md`、`memories/MEMORY.md`、`memories/USER.md`が対象です。会話DB、作業文書、ホーム全体は同期しません。本人用skillsは参照資料やスクリプト等を含むpackageとして別に同期します。受信した会社skillsと本人用skills、同梱の`myhermes-skills`・`myhermes-connections`を同じHermesから利用できます。

`start --offline`は同期を省略して手元の作業コピーを使う指定です。オフラインで推論モデルを提供する指定ではなく、モデル利用には引き続き会社relayへの通信が必要です。進行中のセッションへ別の同期コマンドでファイルを上書きすることはできません。

## 5. GitHubアカウントをつなぐ

ポータルの「アカウントと連携」で、利用可能なテンプレートのIDとversionを確認します。Codexへ`connect-service` skillとその2項目、対象リポジトリ、アカウントの区分を渡します。Codexは認証付きテンプレート取得と秘密を含まない設定確認を行います。

PAT入力は、案内されたコマンドを本人のネイティブ端末で実行して行います。GitHub側で本人が許可したfine-grained PATを使い、対象リポジトリを明示します。トークンをCodex、環境変数、コマンド引数、ファイルへ渡しません。現在のコネクタはリポジトリ情報とissueの読み取りに対応します。投稿・更新機能はありません。

個人アカウントの登録時には、許可する範囲で業務に利用する意思を記録します。個人用という理由だけで毎回追加承認を求めることはありません。この登録で、管理者や他の人がアカウントの内容を閲覧・代理利用できるようになることもありません。

同じサービスの複数アカウントを追加できます。Hermesへの依頼では、接続IDと対象リポジトリを指定してください。2台目の環境では人格や本人用skillsが共有されてもPATは共有されません。その環境で同じ論理接続を再認可します。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" connections list
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" connections show CONNECTION_ID
```

未認可・失効・対象外の接続は利用できません。別アカウントの広い資格情報へ自動で切り替えることはありません。途中で通信が切れた場合は`connections pending`で確認し、同じ要求を`connections resume REQUEST_ID`で再開します。[接続と復旧](CONNECTIONS.md)に各コマンドの条件があります。

## 6. 2台目と日常の確認

2台目でも手順1〜4を実施し、同じ本人として承認します。別のhome・状態DB・鍵・installation IDを持たせます。1台目の状態ディレクトリや秘密鍵をコピーしません。

人格の編集や削除、履歴と競合の確認は、ポータルの本人用画面から行えます。同じファイルを別環境で変更した場合は両方を保持し、本人が選びます。管理者へ内容が公開されることはありません。削除は現在版へのtombstoneを作り、過去の履歴は残ります。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" inspect
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" sync --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" conflicts
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" monitoring inspect
```

これらは本文を出力しない診断です。通信断では未送信データを残して再送します。競合を消すために状態DBを削除したり、人格ファイルを空にしたりしないでください。監視の送信状態と人格の同期状態は別です。実際の収集項目は`monitoring manifest`とポータルの「同期と収集の範囲」で確認できます。

ポータルの「人格の適用報告」は、その端末が特定revisionを適用したという過去の申告です。現在のローカル編集やHermesへの読み込みを管理者が確認できる機能ではありません。受理済みrevisionや最後に認証した通信時刻は、サーバーの観測として別に表示します。[公開された適用報告仕様](SYNC_STATUS.md)を確認できます。

更新・バックアップ・復旧とexit codeの詳細は[COMPANION.md](COMPANION.md)、本人用skillsの作成・派生は[SKILL_SYNC.md](SKILL_SYNC.md)を参照してください。環境失効で既に取得した手元の内容が消えるわけではありません。失効後は、新しい鍵での本人登録が必要です。

## 7. 失効・鍵の紛失後に同じ環境を再登録する

同じ本人のhomeと状態DBを保持している場合は、本人のネイティブ端末で次を実行します。古い鍵を読み出せなくても再登録できます。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" re-enroll --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" re-enroll
```

元と同じ本人としてブラウザで承認します。別の本人が承認した場合は内容を送る前に停止します。通信断や中断後は、同じコマンドで同じ登録要求を再開してください。人格・skillsの競合と送信待ちは保持し、古い環境の署名付き監視記録と接続状態は手元へ退避します。新環境の接続先は再認可が必要です。

再登録自体は古い環境を失効しません。不要な登録は本人のポータルで失効します。承認する本人を間違えた場合や次回の鍵更新は、[同じhomeの再登録手順](IDENTITY_RECOVERY.md)の`--cancel`・`--new`の条件に従ってください。
