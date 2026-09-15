# MyHermes 利用者ガイド

MyHermesを使うと、Hermesへの指示、永続メモリ、本人用skillsを自分の複数の端末で共有できます。
日々の作業は端末のHermesで行い、ブラウザのポータルで登録環境や同期内容を管理します。
会社、取引先、個人のアカウントを使い分けるために、人格を別々に作る必要はありません。

このガイドのサーバーURLは架空の例です。
管理者から案内されたURLに置き換えてください。

## 使う場所とできること

| 場所 | できること |
| --- | --- |
| 端末のHermes | 資料の要約、文章の下書き、データ処理、コードの作成や調査を会話で依頼する |
| Dockerサンドボックス | Hermesがコマンドを実行し、渡された作業ファイルを読み書きする |
| MyHermesポータル | 本人の人格やメモリの編集、環境登録、同期履歴、skills、接続設定、利用統計を確認する |

標準起動ではDockerサンドボックスを使います。
**Dockerが使えない場合は起動を止め、端末上での直接実行へ自動で切り替えません。**
Hermes本体、永続メモリ、認証や同期の処理はホスト側で動きます。
隔離する範囲は、コマンド実行、コード実行、それらに対応するファイル操作です。
追加のプラグインや任意のホスト側コードまで、すべてコンテナ内で動く構成ではありません。

## 1. 初回に用意するもの

| 必要なもの | 確認すること |
| --- | --- |
| 対応する端末 | macOSまたはUbuntu。Windows向けの手順はこの版に含みません |
| Git | `git --version`が成功すること |
| Python 3.11〜3.13 | 下の例は3.13。3.11または3.12を使う場合は、各コマンドの`python3.13`を置き換えること |
| Docker | macOSはDocker Desktopなど、UbuntuはDocker Engineなど、会社が認めた環境が起動していること |
| 安全な鍵の保管機能 | macOS Keychain、またはUbuntuのロック解除済みSecret Service |
| 本人の利用権限 | 管理者がMyHermesメンバーとして登録した、本人の認証アカウント |
| インターネット接続 | 初回の取得、会社サーバーへの同期、モデルの利用に必要 |

自分の端末のターミナルを開き、次を確認します。

```sh
git --version
python3.13 --version
docker version
```

`docker version`でServerへ接続できない場合はDockerを起動して再確認します。
必要なソフトがない場合や、UbuntuでSecret Serviceを利用できない場合は、端末の管理者へ導入を依頼してください。
コンパニオンの利用だけならNode.jsは不要です。

初回はサンドボックス用の固定イメージを取得します。
次のコマンドを本人のターミナルで実行し、完了を待ちます。

```sh
docker pull 'nikolaik/python-nodejs:python3.11-nodejs20@sha256:8f958bdc1b4a422bfafd97cab4f69836401f616ae985d4b57a53d254f5bcb038'
```

通常の`start`はイメージを自動でダウンロードしません。
Dockerはこの端末の環境を使い、Hermes homeを保存するユーザーホームがDockerのファイル共有対象に含まれていることを確認してください。
リモートのDockerサーバーへの接続はこの構成では使いません。

## 2. コンパニオンをインストールする

**コンパニオン**は、登録、同期、Hermesの起動を行う`myhermes`コマンドです。
公開リポジトリの`main`から取得し、このコマンド専用のPython環境へインストールします。

```sh
git clone --branch main https://github.com/nyasukun/myhermes-starter.git "$HOME/myhermes-starter"
cd "$HOME/myhermes-starter"
python3.13 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/myhermes --version
```

すでに`$HOME/myhermes-starter`がある場合は、上書きや削除をせず、後述の更新手順を使います。
管理者が特定のcommitや検証済みwheelを指定している場合は、その配布版を使ってください。
PyPIに公開済みであることを前提としたインストールは案内していません。

以降のコマンドは、`cd "$HOME/myhermes-starter"`を実行したターミナルで使います。
保存先は次のとおりです。

| 保存先 | 内容 |
| --- | --- |
| `$HOME/myhermes-starter` | 公開コンパニオンのプログラム |
| `$HOME/.local/state/myhermes-work` | この端末の登録情報、同期の状態、送信待ちの変更 |
| `$HOME/.local/share/myhermes-work/home` | 本人の人格、メモリ、skills、Hermesのローカル履歴 |
| `$HOME/.local/share/myhermes-work/runtime` | 対応する公式Hermesの固定版 |

本人の状態やHermes homeをGitへ追加したり、ファイル共有サービスで丸ごと別の端末へコピーしたりしないでください。

## 3. この端末を設定する

サーバーURLを管理者の案内に置き換え、環境名を自分が区別できる名前にします。
`--dry-run`は設定予定の確認です。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" setup \
  --server https://myhermes.example.com \
  --hermes-home "$HOME/.local/share/myhermes-work/home" \
  --upstream "$HOME/.local/share/myhermes-work/runtime" \
  --label 'My work laptop' --dry-run
```

OS、URL、保存先を確認し、同じコマンドから`--dry-run`を外して実行します。
続けて対応する公式Hermesを導入します。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" install-runtime --python python3.13
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" inspect
```

`install-runtime`は公開プログラムやPythonパッケージを取得するため、初回は時間がかかります。
途中で失敗した場合は、原因を解消して同じコマンドを再実行します。
既存のHermes homeを削除する必要はありません。

## 4. 本人としてログインし、端末を登録する

管理者から案内されたポータルをブラウザで開き、本人のアカウントでログインします。
本人の画面を開けない場合は、管理者にメンバー登録を確認してもらってください。

次のコマンドは、**本人が自分のネイティブ端末で実行**します。
Codexが捕捉するツールやPTYでは実行しません。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" enroll
```

1. 開いたブラウザで、登録する環境名とOSを確認します。
2. ターミナルに表示された一度限りの確認コードを、そのブラウザ画面に入力します。
3. 「この環境を登録」を押し、ターミナルへ戻って完了を待ちます。
4. 次のコマンドで`installation_id`が表示され、ポータルの「登録環境」に自分の端末があることを確認します。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" inspect
```

確認コード、秘密鍵、GitHubトークンをCodexやHermesの会話へ貼る必要はありません。
秘密鍵はOSの安全な保管機能へ保存されます。
この機能が使えない場合は登録を止め、平文ファイルへ代替保存しません。

ブラウザで承認する前にターミナルの待機が終わった場合は、同じ`enroll`を再実行します。
ブラウザが自動で開かない場合は`enroll --no-browser`を実行し、本人のターミナルに表示されたURLを開いてください。

## 5. 毎日の起動と終了

Dockerを起動し、ターミナルで次を実行します。
初回だけ、先に`start --dry-run`で起動予定を確認できます。

```sh
cd "$HOME/myhermes-starter"
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" start
```

起動前に人格とskillsを同期してから、Hermesの会話が始まります。
モデルの利用には、管理者が設定した会社relayが必要です。
会社のOpenRouterキーを本人の端末へ設定する必要はありません。
最初は「利用できるツールを確認して、短く自己紹介してください」のような依頼で応答を確認します。

作業が終わったらHermesを終了し、ターミナルに終了結果が戻るまで待ってください。
終了後にも人格とskillsを同期します。
**Hermesの終了と、その後の同期の完了は別々に結果へ表示されます。**
通信に失敗した変更は手元に残るので、接続回復後に次を実行します。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" sync
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" skills sync
```

`start --offline`は起動前の同期を省略する指定です。
モデル利用には引き続き会社relayへの通信が必要で、Dockerも必要です。

## 6. サンドボックスへ資料を渡し、成果物を受け取る

Hermesのコマンドとファイル操作は、Docker内の`/workspace`を作業場所にします。
MacやUbuntuのデスクトップ、ダウンロードフォルダー、ホーム全体は自動で共有しません。
作業に必要なファイルだけを、このセッションの専用workspaceへコピーして渡します。
Hermesのskillsや専用キャッシュ・添付資料は、機能を提供するためコンテナにも共有されます。
任意の外部フォルダーや資格情報を共有する設定は、標準起動では受け付けません。

まずHermesへ「terminalで`pwd`を実行し、`/workspace`で作業できることを確認してください」と依頼します。
次に別のターミナルを開き、動作中のコンテナを表示します。

```sh
docker ps --format 'table {{.ID}}\t{{.Names}}'
```

表示されたIDを下の`CONTAINER_ID`に入れ、作業場所を確認します。
出力が今回設定したHermes homeの`myhermes-sandboxes/docker/`配下を指しているコンテナを選びます。
複数あって対象を特定できない場合は、別のセッションを終了するか、管理者に確認してください。

```sh
docker inspect CONTAINER_ID --format '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}'
```

たとえばダウンロードフォルダーの資料を渡す場合は、ファイル名とIDを置き換えて実行します。

```sh
docker cp "$HOME/Downloads/meeting-notes.md" CONTAINER_ID:/workspace/meeting-notes.md
```

Hermesには`/workspace/meeting-notes.md`を読むよう依頼します。
成果物を`/workspace/output/`へ保存させたら、**Hermesを終了する前に**次のように取り出します。

```sh
docker cp CONTAINER_ID:/workspace/output "$HOME/Downloads/myhermes-output"
```

同名の保管先がすでにある場合は、別のフォルダー名を選びます。
終了後も、上の`docker inspect`で確認したホスト側のworkspaceには保存したファイルが残ります。
専用workspaceはHermes homeの`myhermes-sandboxes/docker/`配下へ保存されます。
作業後は成果物を通常の保管先へ移し、次回の会話や別の端末へ自動で引き継がれるとは考えないでください。
作業ファイルはMyHermesの同期対象に含みません。

## 7. 依頼の例

資料をworkspaceへ渡した後、目的、入力、出力をまとめて伝えます。

**資料を要約する**

> `/workspace/meeting-notes.md`を読み、決定事項、未決事項、担当者と期限を整理してください。
> 資料に書かれていない担当者や期限は「未定」とし、結果を`/workspace/output/summary.md`へ保存してください。

**文章の下書きを作る**

> `/workspace/product-notes.md`をもとに、既存のお客様向けの案内文を日本語で作成してください。
> 変更点、利用者が必要な操作、問い合わせ先の順にまとめ、確認が必要な箇所を末尾へ一覧にしてください。
> 下書きを`/workspace/output/announcement.md`へ保存してください。

**CSVを調べる**

> `/workspace/sample-sales.csv`の列と欠損値を確認し、月ごとの売上を集計してください。
> 元ファイルを残し、集計結果と計算条件を`/workspace/output/`へ保存してください。

**回答の好みを覚えてもらう**

> 回答は日本語で、結論、根拠、次の作業の順に簡潔にまとめてください。
> 今後にも使う私の希望として、永続メモリへ保存してください。

出力の数値、引用、固有名詞を確認してから業務で使います。
本人の永続メモリには、パスワードやトークン、保存する必要のない機密情報を書かないでください。

## 8. ポータルの使い方

| 画面 | 主な操作 |
| --- | --- |
| 自分の人格・メモリ | `SOUL.md`、`memories/MEMORY.md`、`memories/USER.md`を選んで編集し、「変更を保存」する |
| 登録環境 | 自分の端末、最後の通信、人格の適用報告を確認し、不要な環境を失効する |
| 履歴と競合 | 過去の内容と、同じファイルに対する競合を確認し、残す内容を選ぶ |
| アカウントと連携 | 接続テンプレート、登録したアカウント、許可するリポジトリや操作を確認する |
| Hermes skills | 会社から配布されたskillsの確認、本人用skillの作成や編集を行う |
| 利用統計と監査 | 本人の利用記録と、サーバーで観測したモデル利用量を確認する |
| 同期と収集の範囲 | 同期される内容、管理者へ見える情報、収集対象を確認する |

人格やskillsの変更は、Hermesを終了してから保存し、次回の起動で使うのが基本です。
実行中の同じhomeへ別の同期コマンドで変更を適用することはできません。
複数端末で同じ内容を同時に編集した場合は両方の変更を保持するため、「履歴と競合」で本人が選択します。
競合を消すために状態ディレクトリを削除しないでください。

「人格の適用報告」は、端末がその版を適用したと申告した記録です。
現在の手元の編集内容や、Hermesが読み込んだ内容まで保証する表示ではありません。

## 9. Skillsを使う

繰り返す作業の手順は**skill**として保存できます。
会社が配布したskillと本人用skillは、同じHermesから利用できます。

1. ポータルの「Hermes skills」で利用できるskillの名前と説明を確認します。
2. Hermesを終了した状態で`skills sync`を実行するか、次回の`start`で同期します。
3. Hermesへ「利用できるskillsを確認し、○○のskillを使ってこの資料を整理してください」と依頼します。

本人用を作る場合は「本人用skillを作る」を選び、識別名、版、説明、手順を入力して保存します。
参照資料やスクリプトを添付する場合は、必要なファイルをすべて含めます。
保存済みの内容を変更するときは`1.0.0`から`1.0.1`のように新しい版を付けてください。
本人用skillが自動で会社全体へ公開されることはありません。

ローカルで作ったskillを同期する操作は、ホスト側の`myhermes skills import`で行えます。
Docker内のコマンドからホストの認証情報へアクセスさせず、[skillの作成と同期](SKILL_SYNC.md)の手順を使ってください。
Hermesが作った未登録のskillディレクトリは、そのままではMyHermesの同期対象になりません。

## 10. GitHubアカウントを接続する

現在の組み込み接続はGitHubのリポジトリ情報とissueの読み取りに対応します。
投稿や更新はこのコネクタの対象に含みません。

1. ポータルの「アカウントと連携」でテンプレートのIDとversionを確認します。
2. 接続するアカウントの区分と、許可する`owner/repository`を決めます。
3. このリポジトリを開いたCodexに`connect-service` skillを指定し、上の情報を渡して、秘密情報を含まない設定確認を依頼します。
4. 案内された認可コマンドを本人のネイティブ端末で実行し、GitHubで本人が発行したfine-grained PATを入力します。

トークンは環境変数、コマンド引数、ファイル、会話へ貼り付けません。
会社用、取引先用、個人用の接続を複数登録でき、利用時は接続IDと対象リポジトリを明示します。
別の接続へ自動で切り替えて権限を広げることはありません。

接続の状態は、ホスト側のターミナルで確認できます。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" connections list
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" connections show CONNECTION_ID
```

Dockerサンドボックスからは、ホストの`myhermes`コマンドやKeychainをそのまま使えません。
この構成ではホスト側で接続を操作し、必要な取得結果だけを明示的にファイルへ書き出して、workspaceへ渡します。
読み取りや再認可の正確なコマンドは[接続の操作](CONNECTIONS.md)を参照してください。

## 11. データの扱い

| データ | 保存や送信の範囲 |
| --- | --- |
| 人格と永続メモリ | 指定の3ファイルをクラウドの本人領域へ保存し、本人の登録環境で同期する |
| 本人用skills | 明示的に登録したpackageを本人領域で同期する。添付した参照資料やスクリプトも含む |
| 会話履歴と作業資料 | MyHermesの人格同期では送信しない。Hermesがローカルに履歴を保存することはある |
| モデルに渡す情報 | 会話、必要な人格やメモリ、読み込んだ資料やツール結果は、推論のため会社relayとモデル提供者へ送信される |
| 管理者が確認できる情報 | 所定の利用者や環境のメタデータ、利用件数、時間、モデル利用量など。通常の管理者APIから本人用の本文は取得できない |
| 秘密鍵とGitHubトークン | 各端末のOSの安全な保管機能に保存し、別の端末へ同期しない |

サンドボックス内の資料も、モデルに読ませると推論先へ送信されます。
入力する資料は会社のデータ取扱いルールに従って選んでください。
アプリのrelayや監視ログは本文を保存しませんが、クラウド上の本人用同期領域はエンドツーエンド暗号化ではありません。

ポータルでの削除は削除履歴を残し、過去の版やバックアップを直ちに消去する操作ではありません。
環境の失効も、取得済みの内容を端末から遠隔消去する機能ではありません。
詳細は[データの取扱い](PRIVACY.md)にあります。

## 12. 更新と2台目の追加

### コンパニオンとHermesを更新する

Hermesを終了し、管理者の更新案内を確認してから実行します。

```sh
cd "$HOME/myhermes-starter"
git status --short
```

変更がある場合は、それを残したまま管理者に確認します。
変更がなく、案内された更新先が`main`の場合は次を実行します。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" backup
git pull --ff-only origin main
.venv/bin/python -m pip install .
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" upgrade --python python3.13
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" start --dry-run
```

`upgrade`はこのコンパニオンが対応する公式Hermesの固定版を導入または修復します。
Hermes本体だけを独自に最新化せず、対応版を使ってください。
`backup`は同期対象の人格3ファイルを保存するもので、作業資料や会話履歴のバックアップには含みません。

### 2台目を追加する

2台目でも手順1〜5を実施し、同じ本人として承認します。
それぞれの端末に独立したhome、状態ディレクトリ、鍵、installation IDが作られます。
人格と本人用skillsは共有できますが、作業ファイルとGitHubトークンは自動で移りません。
GitHub接続を使う端末では、その端末で再認可します。

## 13. 困ったとき

| 症状 | 対処 |
| --- | --- |
| ポータルへログインできない、403になる | 本人のアカウントか確認し、管理者にメンバー登録とAccessの許可を確認してもらう |
| Docker関連のエラーで起動しない | Dockerを起動し、ホスト端末の`docker version`でServerへ接続できるか確認する |
| `runtime_sandbox_image_missing` | 手順1の`docker pull`で固定イメージを取得する |
| `runtime_sandbox_mount_unavailable` | Hermes homeの保存先がDockerへ共有されているか、端末管理者と確認する |
| 既存の環境変数や資格情報の共有設定が拒否された | 管理者と既存設定を確認し、バックアップしてから不要な共有設定を外す。機密情報をコンテナへ渡して起動を通さない |
| `secure_store_unavailable` | KeychainまたはSecret Serviceを確認する。平文保存へ変更せず、端末管理者へ相談する |
| ブラウザで登録したが待機が終わっている | 同じ端末で`enroll`を再実行して完了を確認する |
| 更新の取得やインストールが途中で失敗した | 通信とPythonの版を確認し、同じ操作を再実行する。既存のhomeを消さない |
| 同期が完了しない | Hermesを終了し、通信回復後に`sync`と`skills sync`を実行する |
| 同期の競合がある | ポータルで双方を比較する。状態DBの削除や空ファイルでの上書きはしない |
| skillを変更したら起動できない | 変更した内容を残し、別の版としてimportするか、会社skillから本人用の派生版を作る |
| GitHub接続が未認可になっている | 接続IDと対象環境を確認し、その端末のネイティブ端末で再認可する |
| `relay_not_configured`、モデルの利用量や予算のエラー | エラー識別子と発生時刻を添え、管理者へ会社relayの設定や利用枠の確認を依頼する |
| 鍵を失った、環境を失効した | 同じ本人のhomeを保持し、下の再登録手順を使う |

同じ端末を再登録するときは、本人のネイティブ端末で実行します。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" re-enroll --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" re-enroll
```

元と同じ本人として承認します。
再登録しても古い環境は自動で失効しないので、不要な登録はポータルで失効してください。
送信待ちの人格やskillsは保持しますが、外部接続は新しい環境で再認可が必要です。
中断や取消の条件は[同じhomeの再登録](IDENTITY_RECOVERY.md)を参照してください。

管理者へ状況を伝えるときは、実行したコマンド名、OS、発生時刻、エラー識別子を添えます。
次のコマンドは本文を表示しない診断です。
出力には環境IDなどのメタデータを含むため、会社が指定した窓口で共有してください。

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" inspect
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" monitoring inspect
```

人格や会話本文、資料、コード、鍵、トークンを丸ごと診断用に送る必要はありません。
検証済みOSの範囲は[OS_CHECKS.md](OS_CHECKS.md)、コマンドと復旧の詳細は[COMPANION.md](COMPANION.md)にあります。
