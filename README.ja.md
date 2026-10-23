<p align="center"><img src="assets/images/wordmark.png" alt="GazeFace" height="56"></p>

GazeFace は、Web カメラだけで Windows を操作するためのアプリです。頭の動きでマウスポインタを動かし、表情（口を開ける、眉を上げる、目を閉じる など）でクリックやキー入力を行います。視線を使って画面上の離れた場所へ一気に移動することもできます。

手や腕を使ったマウス・キーボード操作が難しい方が、顔だけでパソコンを使えることを目指しています。

*English: [README.md](README.md)*

## 主な機能

- **ヘッドトラッキング**: 頭の動きでポインタを移動
- **表情ジェスチャー**: 左・右・中クリック、ダブルクリック、押しっぱなし（ドラッグ）、キー入力やショートカットを表情に割り当て
- **視線ジャンプ**: 見ている場所の近くへポインタを移動。近くのリンクやボタンに吸着させることも可能
- **視線移動**: 画面の矢印を見つめた方向へポインタを動かす
- **視線ランチャー / タスクスイッチャー / クリップボード履歴**: 画面を区切ったタイルを見つめて選ぶツール
- **サインイン画面・UAC 画面での利用**: Windows の支援技術として登録し、サインイン前や管理者の確認画面でも操作できる超軽量版を起動
- **プロファイル**: 人や場面ごとに設定を切り替え

## 動作環境

最低:

- Windows 10 / 11（64 ビット）
- CPU: 4 コア（Intel Core i5 第 8 世代 / AMD Ryzen 5 3000 シリーズ以上）
- メモリ: 8 GB
- ストレージ: 空き 1 GB
- GPU: Direct3D 11 に対応した内蔵 GPU（Intel UHD 620 / AMD Radeon Vega 以上）
- Web カメラ: 720p、30 fps

推奨:

- CPU: 6 コア以上
- メモリ: 16 GB
- 使う場所の明るさでよく映る Web カメラ

顔の検出は CPU で行い、CPU の 1 スレッド分ほどを使い続けます。GPU は画面に重ねて表示する部分の描画にだけ使います。

## インストール

[Releases](../../releases) から `GazeFace-Setup-<バージョン>.exe` をダウンロードして実行します。`C:\Program Files\GazeFace` にインストールされ、スタートメニューから起動できます。初回起動時に視線キャリブレーションの案内が表示されます。

インストーラーはまだコード署名していないため、Windows SmartScreen の警告が出ることがあります。「詳細情報」→「実行」で続けてください。

「実行」の選択肢が出ずにブロックされる場合は、スマート アプリ コントロールがオンになっている可能性があります。「Windows セキュリティ」→「アプリとブラウザーの制御」→「スマート アプリ コントロールの設定」で確認できます。オフにすると GazeFace のような署名のないアプリを実行できるようになりますが、一度オフにすると Windows を入れ直すまでオンに戻せません。S モードの Windows には GazeFace をインストールできません。

GazeFace は起動中に 1 日 1 回新しいバージョンを確認し、見つかるとウィンドウ内でお知らせします。「今すぐ更新」を選ぶまでは何もインストールしません。選ぶと確認画面なしで更新し、GazeFace が自動で再起動します。

アンインストールは「設定」→「アプリ」から行います。

## プライバシー

- カメラ映像はパソコンの中だけで処理し、保存も外部への送信もしません。
- 顔のランドマーク（目・鼻・口などの位置）を検出しますが、顔認証や個人の識別は行いません。
- 設定とログは `%LOCALAPPDATA%\GazeFace` に保存されます。

## ソースからのビルド

Python 3.12（64 ビット）を使います。

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
.venv\Scripts\python run_app.py
```

配布用の実行ファイルは PyInstaller で作成します。

```bash
.venv\Scripts\pyinstaller build.spec --noconfirm
```

初回起動時は `configs_template` の内容が設定の初期値になります。

## クレジット

私は四肢麻痺です。[Project Gameface](https://github.com/google/project-gameface) が無ければ、私が仕事を続けることはできませんでした。Project Gameface を開発し、オープンソースとして公開してくださった Google と関係者の皆さまに、心から感謝します。GazeFace は、同じ機会を他の人にも届けたいという思いから、その成果を元に生まれました。

- [Project Gameface](https://github.com/google/project-gameface)（Google LLC） — GazeFace はこのプロジェクトを元に改変したものです
- [MediaPipe Face Landmarker](https://developers.google.com/mediapipe/solutions/vision/face_landmarker) — 顔のランドマークと表情の検出
- [IBM Plex](https://github.com/IBM/plex) — フォント

## ライセンス

[Apache License 2.0](LICENSE)。改変と同梱物の詳細は [NOTICE](NOTICE) を参照してください。フォントは SIL Open Font License 1.1（[assets/fonts/OFL.txt](assets/fonts/OFL.txt)）です。
