# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Source-string -> localized-string mappings.

Keep keys identical to the English source so missing translations fall back
gracefully via the i18n.t() lookup.
"""

JA = {
    # ---- Window / chrome ----
    "Project Gameface": "Project Gameface",
    "Project Gameface Gesture Settings": "Project Gameface ジェスチャ設定",
    "Project Gameface helps gamers control their mouse cursor using their head movement and facial gestures.":
        "Project Gameface はゲーマーが頭の動きと表情ジェスチャでマウスカーソルを操作するためのアプリです。",
    "Disclaimer: Project Gameface is not intended for medical use.":
        "免責事項: Project Gameface は医療用途を意図していません。",

    # ---- Menu (left sidebar) ----
    "Advanced": "詳細設定",

    # ---- Cam preview frame ----
    "Face control": "顔操作",
    "Allow facial gestures to control\nyour actions. ":
        "表情ジェスチャでアクションを操作することを\n許可します。",

    # ---- Cursor speed page ----
    "Cursor speed": "カーソル速度",
    "Mouse cursor moves with your head movement. Use this settings to adjust how fast your mouse moves in each direction.":
        "頭の動きに合わせてマウスカーソルが動きます。各方向の移動速度はここで調整します。",
    "Move up": "上方向",
    "Move down": "下方向",
    "Move right": "右方向",
    "Move left": "左方向",
    "Smooth pointer": "ポインタ スムージング",
    "Hold trigger delay(ms)": "ホールド判定遅延 (ms)",
    "Controls the smoothness of the\nmouse cursor. Enables the user\nto reduce jitteriness":
        "マウスカーソルの滑らかさを調整します。\n細かなブレを抑えられます。",
    "Reduces the flickering of the action\ntrigger":
        "アクション判定のチラつきを軽減します。",
    "Controls how long the user should\nhold a gesture in milliseconds\nfor an action to trigger":
        "アクション発火までに表情を保持する\nミリ秒数を制御します。",
    "Caution: raising the speed too high makes the cursor uncontrollable. Adjust in small steps.":
        "注意: 速度を上げすぎると操作不能になります。少しずつ調整してください。",

    # ---- Camera page ----
    "Camera": "カメラ",

    # ---- FacePilot 仕上げ: 未翻訳解消 ----
    "Tick the gestures you want available in the bindings dropdown. The bar shows the gained blendshape value (= number compared against the threshold). The number on the right is the per-gesture sensitivity multiplier.":
        "割り当てドロップダウンに表示するジェスチャにチェックを入れます。バーはゲイン適用後の値（しきい値と比較される数値）、右の数値はジェスチャごとの感度倍率です。",
    "Tick modifiers, then press the main key.": "修飾キーにチェックを入れ、メインのキーを押してください。",
    "Mouse step": "マウスステップ",
    # ---- ドラッグ / スマホ操作 ----
    "Drag left": "ドラッグ 左",
    "Drag middle": "ドラッグ 中",
    "Drag right": "ドラッグ 右",
    "Phone gesture": "スマホ操作",
    "Phone": "スマホ",
    "Wheel": "ホイール",
    "Notches": "回転数",
    "Long press": "長押し",
    "Add long press": "長押しを追加",
    "Hold (ms)": "長押し時間 (ms)",
    "Swipe": "スワイプ",
    "Short": "小",
    "Medium": "中",
    "Long": "大",
    "Custom (px)": "カスタム (px)",
    "Left": "左",
    "Middle": "中",
    "Right": "右",
    "Scan key": "キースキャン",
    "Preset": "プリセット",
    "Wait": "待機",
    "Wait (ms)": "待機 (ms)",
    "Key": "キー",
    "Text": "テキスト",
    "Text to type": "入力するテキスト",
    "None": "なし",
    "Key / Mouse / Macro": "キー / マウス / マクロ",
    "User profiles": "ユーザープロファイル",
    "left": "左クリック",
    "right": "右クリック",
    "middle": "中クリック",
    "double": "ダブルクリック",

    # ---- Left nav (FacePilot design) ----
    "Home": "ホーム",
    "Cursor": "カーソル",
    "Mouse": "マウス",
    "Keyboard": "キーボード",
    "Add profile": "プロファイルを追加",
    "Click a profile to use it.": "クリックでプロファイルを切替",
    "Select a Camera": "カメラを選択",

    # ---- Mouse binding page ----
    "Mouse binding": "マウス バインディング",
    "Select a facial gesture that you would like to bind to a specific mouse action. Sensitivity allows you to control  the extent to which you need to gesture to trigger the mouse action":
        "特定のマウス操作に割り当てる表情ジェスチャを選びます。しきい値で「どれくらい表情を作れば反応するか」を調整します。",
    "Mouse left click": "左クリック",
    "Mouse right click": "右クリック",
    "Mouse middle click": "中クリック",
    "Mouse double click": "ダブルクリック",
    "Mouse pause / unpause": "一時停止 / 再開",
    "Reset cursor to center": "カーソルを中央へ戻す",
    "Switch focus between monitors": "モニタを切り替える",
    "Gaze Jump": "視線ジャンプ",
    # Gesture names
    "Close left eye": "左目を閉じる",
    "Close right eye": "右目を閉じる",
    "Cheek puff": "頬を膨らます",
    "Smile left": "微笑む (左)",
    "Smile right": "微笑む (右)",
    "Frown left": "しかめっ面 (左)",
    "Frown right": "しかめっ面 (右)",
    "Mouth pucker": "口をすぼめる",
    "Jaw forward": "顎を前へ",
    "Jaw left": "顎を左へ",
    "Jaw right": "顎を右へ",
    "Squint left eye": "左目を細める",
    "Squint right eye": "右目を細める",
    "Wide left eye": "左目を見開く",
    "Wide right eye": "右目を見開く",
    # Mouse-binding row buttons + popup labels
    "Action options": "機能オプション",
    "Gesture options": "表情オプション",
    "No action-specific options here yet.":
        "この機能に固有のオプションは現在ありません。",
    "Coming soon.": "開発中です。",
    "These settings stay with the gesture and carry over even if you bind it to a different action later.":
        "これらの設定はジェスチャに紐づきます。割り当てる機能を変えても引き継がれます。",
    "Sensitivity (per-gesture)": "感度 (ジェスチャ別)",
    "Multiplier applied just to this gesture, on top of the global sensitivity. 1.0 = no change.":
        "グローバル感度に上乗せで、このジェスチャだけに掛かる倍率です。1.0 で変更なし。",
    "Hold time (ms)": "保持時間 (ms)",
    "Freeze pointer on gesture": "ジェスチャー時ポインタ停止",
    "When this gesture's value changes faster than the sensitivity below, the cursor freezes instantly so the act of making the gesture doesn't drag the pointer. It releases the moment the value settles.":
        "このジェスチャの値が下の感度より速く変化したとき、カーソルを即座に停止し、ジェスチャを作る動作でポインタが動かないようにします。値が落ち着いた瞬間に解放されます。",
    "Freeze sensitivity": "停止感度",
    "Lower = more sensitive (freezes on smaller motion).":
        "小さいほど高感度 (わずかな動きで停止)。",
    "When this gesture's value rises faster than the sensitivity below, the cursor freezes and stays frozen until the value returns to where it started — so the act of making the gesture doesn't drag the pointer. Auto-releases after 10 s as a safety.":
        "このジェスチャの値が下の感度より速く上昇したとき、カーソルを停止し、値が元に戻るまで停止を維持します (ジェスチャ動作でポインタが動かないように)。安全のため10秒で自動解放されます。",
    # Simultaneous-input block
    "Block simultaneous input": "同時入力禁止",
    "While this gesture's value is moving, ignore input from other gestures with a lower priority. Use it to stop a faint twitch from firing while you make a deliberate gesture.":
        "このジェスチャの値が動いている間、優先順位の低い他のジェスチャの入力を無視します。意図的なジェスチャ中に、わずかなピクつきで誤発火するのを防ぎます。",
    "Movement sensitivity": "検出感度",
    "Lower = treats smaller motion as 'moving'.":
        "小さいほど、わずかな動きを「動いている」と判定。",
    "Priority (higher wins)": "優先順位 (高いほど優先)",
    "Response curve": "反応カーブ",
    "Shape of how the gesture value rises with intensity. 'Slow start' keeps weak expressions weak and only registers when the gesture is strong. 'Fast start' is the reverse — responsive to tiny motion, plateaus near full.":
        "ジェスチャの強さに対する反応カーブの形状です。「弱→強」は弱い表情では反応せず、強くしたとき一気に値が伸びます。「強→弱」はその逆で、わずかな動きから反応し最大付近で頭打ちになります。",
    "Linear (default)": "リニア (デフォルト)",
    "Slow start, fast end": "弱→強",
    "Fast start, slow end": "強→弱",
    "Required sustained-above-threshold duration before the gesture fires. Set to 0 to fire immediately on rise.":
        "ジェスチャが発動するまでに、しきい値を超え続ける必要がある時間です。0 にすると立ち上がりで即発動。",
    "Close": "閉じる",
    # Gesture list popup
    "Select gesture": "ジェスチャを選択",
    "Click a gesture to bind it to this action. The bar shows the gained blendshape value (= what's compared against the threshold). Greyed-out items are already bound to another action.":
        "ジェスチャをクリックしてこの機能に割り当ててください。バーはゲインを掛けた値 (しきい値判定に使われる値) を示します。灰色の項目はすでに別の機能に割り当て済みです。",
    "Gesture List": "ジェスチャ一覧",
    "Tick the gestures you want to see in the bindings dropdown. Unchecked gestures stay hidden.":
        "バインディングのドロップダウンに表示したいジェスチャにチェックを入れてください。未チェックのものは表示されません。",
    "Select none": "全て解除",
    "Reset to defaults": "デフォルトに戻す",
    "Apply": "適用",
    "Edit gesture list...": "ジェスチャ一覧を編集...",
    "Choose which facial gestures appear in the bindings dropdown. Only ticked gestures show up — uncheck the ones you'll never use.":
        "バインディング ドロップダウンに表示する表情ジェスチャを選びます。チェックが入ったものだけがドロップダウンに出現します。",
    # Updated Advanced debug-panel description (read-only)
    "Read-only live readout. To boost a single gesture, use the 'Gesture options' button on the Mouse-binding page.":
        "読み取り専用のリアルタイム表示です。個別ジェスチャの感度調整はマウスバインディングページの「ジェスチャオプション」ボタンから行ってください。",
    "Gaze calibration is required. Open Advanced settings and run the 16-point calibration first.":
        "視線キャリブレーションが必要です。詳細設定から16点キャリブレーションを実行してください。",
    "Gesture size": "ジェスチャの大きさ",
    "Subtle\t\t\t   Exaggerated": "控えめ\t\t\t  大きく",
    "Set how prominent your gesture has\nto be in order to trigger the action":
        "アクションを発火させるために必要な\nジェスチャの大きさを設定します。",

    # ---- Keyboard page ----
    "Keyboard binding": "キーボード バインディング",
    "Select a facial gesture that you would like to bind to a specific keyboard key. Sensitivity allows you to control the extent to which you need to gesture to trigger the keyboard key press":
        "特定のキー入力に割り当てる表情ジェスチャを選びます。しきい値で「どれくらい表情を作れば反応するか」を調整します。",
    "+ Add binding": "+ 追加",

    # ---- Advanced page ----
    "Advanced settings": "詳細設定",
    "Fine-tune behavior that's not normally needed for day-to-day use.":
        "通常の利用では触らない細かい挙動を調整できます。",
    "Chattering prevention": "チャタリング防止",
    "After any click or key press fires, all further clicks and key presses are blocked for the specified interval. Pointer movement is not affected.":
        "クリックまたはキー入力が発生した後、指定時間が経過するまで全てのクリックとキー入力をブロックします。ポインタの移動は影響を受けません。",
    "Enable chattering prevention": "チャタリング防止を有効化",
    "Interval (ms)": "間隔 (ms)",
    "Tracking mode": "トラッキング方式",
    "Choose how the cursor follows you. Head tracking is the default; gaze tracking uses iris position and benefits from calibration.":
        "カーソルが何を追うかを選びます。デフォルトは頭追跡。視線追跡は虹彩位置を使い、キャリブレーションを行うと精度が上がります。",
    "Head tracking": "ヘッドトラッキング",
    "Gaze tracking": "視線トラッキング",
    "Gaze calibration": "視線キャリブレーション",
    "Required before using the 'Gaze Jump' facial gesture. Maps your gaze to screen positions via a 16-point calibration.":
        "「視線ジャンプ」ジェスチャを使う前に必須です。16点キャリブレーションで視線と画面位置を対応付けます。",
    "Calibrate gaze (16 points)": "視線を16点キャリブレーション",
    "Calibration done. Settings saved.": "キャリブレーション完了。設定を保存しました。",
    "How long to hold the gesture before the cursor snaps to where you're looking. The cursor is frozen during this window.":
        "ジェスチャを保持してから、見ている位置にカーソルがジャンプするまでの時間です。この間カーソルは固定されます。",
    "Dwell time (ms)": "待機時間 (ms)",
    "Gesture sensitivity": "ジェスチャー感度",
    "Multiplies facial-gesture values before the threshold check. Raise this if even your strongest expressions fail to trigger; lower it if subtle face motion causes accidental clicks.":
        "しきい値判定の前に表情ジェスチャ値に倍率を掛けます。大きな表情でも反応しない場合は上げ、僅かな動きで誤反応する場合は下げてください。",
    "Sensitivity (x)": "感度 (倍)",
    "Overall sensitivity": "全体の感度",
    # Action options additions
    "Allow Gaze Jump even when Face control is off": "顔操作がオフでも視線ジャンプを許可",
    "By default Gaze Jump (and every other face-driven action) is suppressed while the Face control toggle in the bottom-left is off. Enable this if you want Gaze Jump specifically to still fire even with Face control off.":
        "デフォルトでは、左下の顔コントロールトグルがオフのとき視線ジャンプ (および全ての顔由来動作) は抑制されます。視線ジャンプだけは顔コントロールオフでも動かしたい場合はこれを有効化してください。",
    # Diagnostics section
    "Diagnostics": "診断",
    "Use when reporting an issue that's hard to reproduce (e.g. head tracking sometimes freezes). Verbose mode writes DEBUG-level events to log.txt; the file rotates so older sessions are kept (log.txt.1 ... log.txt.4).":
        "再現の難しい不具合 (ヘッドトラッキングがたまに固まる等) を報告する際に使ってください。詳細ログ ON で log.txt に DEBUG レベルの情報を出力します。ファイルはローテートされ、過去のセッションは log.txt.1 〜 log.txt.4 として残ります。",
    "Enable verbose logging (effect after restart)": "詳細ログを記録 (再起動後に有効)",
    "Open log folder": "ログフォルダを開く",
    "Auto recovery": "自動復旧",
    "While Face control is ON, if the webcam signal is lost or no face is detected continuously for the configured seconds, the app reopens the camera and rebuilds the face model (a quiet in-process reset — no full restart). If it still can't see a face, it retries every 5 minutes. Face control's on/off state is never changed.":
        "顔コントロールがオンの間、WEBカメラの信号が失われた、または顔が検出できない状態が指定秒数続いた場合、カメラの再接続と顔モデルの再生成を行います (アプリは終了しない内部リセット)。それでも顔が見つからない場合は5分間隔で再試行します。顔コントロールのオン/オフ状態は変更しません。",
    "Enable auto recovery": "自動復旧を有効化",
    "Threshold (seconds):": "閾値 (秒):",
    "Live blendshape values": "表情値リアルタイム表示 (デバッグ用)",
    "Real-time readout of the gesture values MediaPipe produces. The bar shows the value after sensitivity gain (the same number compared against the threshold).":
        "MediaPipe が出力するジェスチャ値のリアルタイム表示です。バーは感度倍率適用後の値 (しきい値判定で実際に使われる値) を示します。",
    "Per-gesture sensitivity: type a multiplier in the rightmost field to boost just that gesture (e.g. 2.0 doubles its value). Effective gain = global × per-gesture.":
        "部位別感度: 右端の入力欄に倍率を入力するとそのジェスチャだけ感度を上げ下げできます (例: 2.0 で 2倍)。実効感度 = グローバル感度 × 部位別感度。",
    "Language": "言語",
    "Changing the language takes effect after restarting the app.":
        "言語の変更はアプリの再起動後に反映されます。",
    "English": "英語",
    "日本語": "日本語",

    # ---- Profile UI ----
    "Profiles": "プロファイル",
    "Edit profiles": "プロファイル編集",

    # ---- Calibration overlay ----
    "Gaze calibration": "視線キャリブレーション",
    "Look at the green dot and hold a neutral facial expression. The dot will move automatically.":
        "緑の点を見つめ、表情はニュートラルに保ってください。点は自動で移動します。",
    "Press ESC to cancel.": "ESC でキャンセル。",
    "Look at the green dots in order.": "緑の点を順番に見つめて下さい",
    "Hold a neutral facial expression.": "表情はニュートラルに保ってください",
    "The dot moves automatically.": "点は自動で移動します",
    "Starting in 2 seconds...": "2秒後に開始します",

    # ---- Misc ----
    "Apply": "適用",
    "Cancel": "キャンセル",
    "OK": "OK",

    # ---- Gesture sub-monitor ----
    "Sub-monitor": "サブモニター",
    "Gesture monitor (always on top)": "ジェスチャモニター (最前面)",
    "No gestures bound": "割り当てられたジェスチャなし",

    # ---- Freeze grace period ----
    "Ignore blinks / involuntary movements": "まばたき・不随意運動を無視",
    "Grace time before the freeze takes effect. A spike that returns to rest within this time is treated as an involuntary movement and ignored.":
        "フリーズが効くまでの猶予時間。この時間内に値が元に戻った急変は不随意運動とみなして無視します。",

    # ---- Sub-monitor opacity (Advanced) ----
    "Opacity of the floating gesture sub-monitor window (toggle it on from the bottom-left of any page).":
        "フローティングのジェスチャサブモニターの透過率（各ページ左下のスイッチで表示）。",
    "Opacity (%)": "透過率 (%)",

    # ---- Multiple instances (Advanced) ----
    "Allow multiple instances (effect after restart)":
        "多重起動を許可（再起動後に反映）",
    "Normally only one copy of the app may run at a time. Enabling this lets several run together — but they will compete for the webcam and can make tracking unstable or corrupt the video. Only enable it if you know you need it (e.g. multiple cameras).":
        "通常はアプリを同時に1つしか起動できません。これを有効にすると複数同時に起動できますが、Webカメラを取り合うためトラッキングが不安定になったり映像が乱れる可能性があります。必要な場合のみ有効にしてください（例: 複数カメラ使用時）。",

    # ---- Gaze move ----
    "Gaze move": "視線移動",
    "Shows 8 direction arrows plus a centre target for the display time below; the arrow you look toward is highlighted. When the time elapses the cursor moves straight that way (looking at the centre cancels). Any gesture stops the move and runs that gesture's action.":
        "下で設定した表示時間の間、8方向の矢印と中央ターゲットを表示し、見ている方向の矢印を強調します。時間経過後、その方向へまっすぐカーソルが移動します（中央を見ているとキャンセル）。何かジェスチャを行うと移動を終了し、そのジェスチャの機能を実行します。",
    "Arrow display time (ms)": "矢印の表示時間 (ms)",
    "Move speed (px/s)": "移動速度 (px/秒)",
    "Start Gaze move right after the jump": "ジャンプ直後に視線移動を起動する",
    "As soon as the cursor snaps to where you looked, the Gaze move direction overlay opens so you can glide the cursor the rest of the way in one motion.":
        "見ていた位置にカーソルが移動した直後、視線移動の方向選択画面が開き、続けて一連の動作でカーソルを目的地まで滑らせられます。",
    # ---- Extensions page ----
    "Extensions": "拡張機能",
    "Gaze-driven tools you can assign to a gesture.":
        "ジェスチャに割り当てられる視線操作ツール。",
    "Gaze launcher": "視線ランチャー",
    "Freezes the pointer and shows a fullscreen icon grid you pick from by gaze, to launch functions. The items can change per foreground app.":
        "ポインタをその場で固定し、画面いっぱいのアイコングリッドを視線で選んで機能を呼び出す。項目はフォアグラウンドのアプリごとに変更可能。",
    "Gaze clipboard history": "視線クリップボード履歴",
    "Pick from saved clipboard text by gaze and paste it into the foreground app.":
        "保存したクリップボードのテキストを視線で選び、フォアグラウンドのアプリに貼り付ける。",
    "Gaze task switcher": "視線タスクスイッチャー",
    "Alt+Tab between windows by gaze.": "視線で Alt+Tab。",
    "Coming soon": "近日対応",
    "Options": "機能オプション",
    "Rows": "行",
    "Columns": "列",
    "Launcher gestures": "表情割り当て",
    "Separate from the main bindings; only gestures already bound in the main menu are listed.":
        "メインの割当とは別。メインメニューで割当済のジェスチャのみ表示。",
    "Confirm": "決定",
    "Back": "戻る",
    "Exit": "終了",
    "Bookmarks": "ブックマーク",
    "Exit GazeFace": "GazeFace を終了",
    "Quit GazeFace?": "GazeFace を終了しますか？",
    "Settings": "設定",
    "Items (click a cell to edit)": "アイテム（セルをクリックで編集）",
    "Register item": "アイテム登録",
    "Cell": "セル",
    "Type": "種別",
    "Title": "タイトル",
    "Keys (e.g. ctrl+c)": "キー（例 ctrl+c）",
    "Mouse action": "マウス機能",
    "Save": "保存",
    "Clear": "クリア",
    "Forward": "進む",
    "Key / macro": "キー / マクロ",
    "Open file": "ファイルを開く",
    "Sub-launcher": "サブランチャー",
    "Sub-launcher name": "サブランチャー名",
    "Extension": "拡張機能",
    "Browse": "参照",
    "File path": "ファイルパス",
    "Arguments (optional)": "引数（任意）",
    "Macro lines: 'key ctrl+c' / 'wait 100' / 'text hello'":
        "マクロ行: 'key ctrl+c' / 'wait 100' / 'text hello'",
    "Clipboard history": "クリップボード履歴",
    "Task switcher": "タスクスイッチャー",
    "App-specific item": "アプリ別アイテム",
    "App": "アプリ",
    "Common items apply to every app; an app-specific item overrides it when that app is in front.":
        "共通アイテムは全アプリで有効。アプリ別アイテムはそのアプリが前面の時に優先。",
    "Icon": "アイコン",
    "Auto": "自動",
    "Choose image": "画像を選択",
    "Page settings": "ページ設定",
    "Open / edit sub-launcher": "サブランチャーを開く / 編集",
    "Sub-monitor toggle": "サブモニター切替",
    "Choose icon": "アイコンを選択",
    "Library": "ライブラリ",
    "Gaze keyboard (test)": "視線キーボード（テスト）",
    "A 50-on soft keyboard you type by gaze. Dwell on a key (or click it) to enter it; Enter copies the text to the clipboard and closes. Test feature — no dakuten or kanji conversion.":
        "視線で入力する五十音ソフトキーボード。キーを見つめる（またはクリック）で入力、Enterで入力文字をクリップボードに送って終了。テスト機能 — 濁音・漢字変換なし。",
    "Gaze keyboard dwell (ms)": "見つめ時間 (ms)",
    "Open gaze keyboard": "視線キーボードを開く",
    "Copy": "コピー",
    "Cut": "切り取り",
    "Paste": "貼り付け",
    "Undo": "元に戻す",
    "Redo": "やり直し",
    "Select all": "すべて選択",
    "Print": "印刷",
    "Find": "検索",
    "Close": "閉じる",
    "Quit": "終了",
    "New": "新規",
    "Open": "開く",
    "New tab": "新しいタブ",
    "Gestures": "表情割り当て",
    "Record clipboard (start monitoring)": "クリップボード記録（監視開始）",
    "Clear history on app start": "アプリ開始時に履歴クリア",
    "Max entries (0 = unlimited)": "保存数（0 で無制限）",
    "Clear now": "今すぐクリア",

    # ---- Dwell confirm, ignore blinks, paging, manage & reorder ----
    "Dwell confirm (ms)": "見つめて確定 (ms)",
    "Open in a separate window": "独立したウィンドウで開く",
    "Startup and window": "起動とウィンドウ",
    "Overlay": "オーバーレイ",
    "Elevated input permission": "上位権限ウィンドウの操作",
    "Allow control of higher-privilege windows (restarts the app)":
        "管理者権限のウィンドウも操作する（アプリを再起動します）",
    "Not available on this installation: %s":
        "このインストールでは利用できません: %s",
    "Input keeps working normally; only the elevated permission is "
    "unavailable.":
        "入力は通常どおり動作します。上位権限だけが使えません。",
    "Active.": "有効です。",
    "Takes effect after the app restarts.": "アプリの再起動後に反映されます。",
    "Ignore blinks (ms)": "まばたきを無視 (ms)",
    "Slider upper limit": "スライダ上限値",
    "Hide tasks": "非表示にするタスクを選択",
    "Refresh overlay every (min, 0 = off)": "オーバーレイ再生成の間隔 (分, 0=無効)",
    "Refresh overlay after sleep or long idle": "スリープ・長時間放置からの復帰時に再生成",
    "Page at startup": "起動時のページ",
    "Last page used": "最後に開いていたページ",
    "Auto-correct gaze drift": "視線ズレを自動補正",
    "Reset drift correction": "自動補正をリセット",
    "Pick from running": "プロセスから選択",
    "Process name": "プロセス名",
    "Window title": "ウィンドウタイトル",
    "Add": "追加",
    "Selection time (ms)": "視線選択時間 (ms)",
    "Background transparency (%)": "背景の透過度 (%)",
    "Show page buttons": "ページ送りボタンを表示",
    "Show pinned-list cell": "ピン留め一覧ボタンを表示",
    "Manage": "管理",
    "Manage / pin": "管理・ピン留め",
    "Color settings": "カラー設定",
    "Background": "背景",
    "Mount": "マウンタ",
    "Highlight": "強調表示",
    "Shared by the launcher, clipboard and task switcher.":
        "ランチャー・クリップボード・タスクスイッチャー共通の設定。",
    "Pinned": "ピン留め",
    "Switch": "リスト切替",
    "Arrow": "矢印",
    "Center ring": "中央リング",
    "Pointer ring": "ポインタリング",
    "History": "履歴",
    "Pin": "ピン留め",
    "Unpin": "ピン解除",
    "Clear selection": "選択解除",
    "Clipboard": "クリップボード",
    "(empty)": "(空)",
    "Manage / reorder": "管理・並び替え",
    "Select items and an operation": "アイテムと並び替え方法を選択",
    "Multi-select": "複数選択",
    "Click the swap / insert destination": "入れ替え・挿入先をクリック",
    "Swap": "入れ替え",
    "Insert": "挿入",
    "Insert blank": "空白を挿入",
    "Deselect": "選択解除",
    "Delete": "削除",
    "Not enough space": "スペースが足りません",
    "Delete selected items?": "選択したアイテムを削除しますか？",
    "Delete this sub-launcher?": "このサブランチャーを削除しますか？",
    "Launchers": "ランチャー一覧",
    "Launcher": "ランチャー",
    "Confirmation": "確認",

    # ---- Stuck-input force release ----
    "Stuck input release": "入力の自動強制解放",
    "If a mouse button or key stays pressed longer than the configured seconds, it is force-released automatically. Protects against a gesture pipeline stall leaving an input held down forever. Set 0 to disable.":
        "マウスボタンやキーが設定秒数を超えて押されたままの場合、自動で強制解放します。ジェスチャ処理の停止で入力が押しっぱなしになるのを防ぎます。0 で無効。",
    "Release after (seconds):": "解放までの秒数:",

    # ---- Left-pane status readout / restart ----
    "Status": "ステータス",
    "Enabled": "有効",
    "Disabled": "無効",
    "Error": "エラー",
    "Restart": "再起動",
    "Restarting": "再起動します",
    "Recovering": "復旧中",
    "Camera error": "カメラエラー",
    "Face detection error": "顔検出エラー",
    "Not responding": "応答なし",
    "exact match": "完全一致",
    # Launcher macro presets (launcher_options_popup.MACRO_PRESETS)
    "Paste plain": "書式なしで貼り付け",
    "To start": "先頭へ",
    "To end": "末尾へ",
    "Replace": "置換",
    "Maximize": "最大化",
    "Minimize": "最小化",
    "Close window": "ウィンドウを閉じる",
    "App switch": "アプリ切替",
    "Cycle windows": "ウィンドウ巡回",
    "Screenshot": "スクリーンショット",
    "Refresh": "更新",
    "Start": "スタート",
    "Left click": "左クリック",
    "Right click": "右クリック",
    "Middle click": "中クリック",
    "Double click": "ダブルクリック",
    "Wheel up": "ホイール 上",
    "Wheel down": "ホイール 下",
    "Long press left": "長押し 左",
    "Swipe up": "スワイプ 上",
    "Swipe up-right": "スワイプ 右上",
    "Swipe right": "スワイプ 右",
    "Swipe down-right": "スワイプ 右下",
    "Swipe down": "スワイプ 下",
    "Swipe down-left": "スワイプ 左下",
    "Swipe left": "スワイプ 左",
    "Swipe up-left": "スワイプ 左上",
    # Help markers, and the gesture names shown in the picker
    "Help for this setting is coming soon.": "この項目のヘルプは後日追加します。",
    "Lower right eyebrow": "右眉を下げる",
    "Lower left eyebrow": "左眉を下げる",
    "browInnerUp": "眉の内側を上げる",
    "Raise right eyebrow": "右眉を上げる",
    "Raise left eyebrow": "左眉を上げる",
    "cheekSquintRight": "右頬を上げる",
    "cheekSquintLeft": "左頬を上げる",
    "eyeLookDownRight": "右目で下を見る",
    "eyeLookDownLeft": "左目で下を見る",
    "eyeLookInRight": "右目で内側を見る",
    "eyeLookInLeft": "左目で内側を見る",
    "eyeLookOutRight": "右目で外側を見る",
    "eyeLookOutLeft": "左目で外側を見る",
    "eyeLookUpRight": "右目で上を見る",
    "eyeLookUpLeft": "左目で上を見る",
    "Open mouth": "口を開ける",
    "mouthClose": "口を閉じる",
    "mouthDimpleRight": "右のえくぼ",
    "mouthDimpleLeft": "左のえくぼ",
    "mouthFunnel": "口をすぼめる",
    "Mouth right": "口を右に寄せる",
    "Mouth left": "口を左に寄せる",
    "mouthLowerDownRight": "下唇の右を下げる",
    "mouthLowerDownLeft": "下唇の左を下げる",
    "mouthPressRight": "口の右を引き結ぶ",
    "mouthPressLeft": "口の左を引き結ぶ",
    "Roll lower mouth": "下唇を巻き込む",
    "Roll upper mouth": "上唇を巻き込む",
    "mouthShrugLower": "下唇を突き出す",
    "mouthShrugUpper": "上唇を突き出す",
    "mouthStretchRight": "口を右に引く",
    "mouthStretchLeft": "口を左に引く",
    "mouthUpperUpRight": "上唇の右を上げる",
    "mouthUpperUpLeft": "上唇の左を上げる",
    "noseSneerRight": "右の鼻にしわを寄せる",
    "noseSneerLeft": "左の鼻にしわを寄せる",
    # ---- Help popup texts (src/gui/help_icon.py) ----
    "Change this when the slider moves unsteadily, or falls back too slowly.":
        "スライダの動きが不安定だったり、戻りが遅かったりする時に変更します。",
    "Holding the gesture for this long switches it on.":
        "指定時間ジェスチャーを保持し続けるとオンになります。",
    "Set this when the slider barely moves and is hard to read.":
        "スライダの動きが小さくて見えにくい時に設定します。",
    "With some gestures head tracking reacts to the movement, so the pointer drifts while you make them. This holds it still, which makes it easier to click exactly where you aimed.":
        "一部ジェスチャーでは、ジェスチャー時にヘッドトラッキングが誤反応しマウスポインタが同時に動いてしまう事があります。これを抑制し、狙った所でクリックなどをしやすくします。",
    "Keeps a blink or an involuntary movement from stopping the pointer by accident.":
        "まばたき・不随意運動で不用意にポインタが停止しないようにします。",
    "Moves the pointer near where you are looking. Webcam gaze detection has limited accuracy, so it can only take you to the rough area; used together with Gaze move or head tracking it gets you there precisely, and quickly.":
        "現在見ている場所の周辺にマウスポインタを移動させます。Webカメラによる視線検出は精度に限界があり、大まかな場所移動しか行えません。視線移動機能やヘッドトラッキングとあわせて使うと細かな移動を素早く行えます。",
    "Makes the fine movement quick. Recommended on.":
        "細かな移動を素早く行えます。オン推奨。",
    "Look at one of the arrows on screen to move the pointer that way. While it is moving, the Gaze move gesture again stops the pointer, and any other gesture immediately runs the action assigned to it.":
        "画面に表示される矢印を見つめるとその方向にマウスポインタを動かせます。移動中にもう一度視線移動ジェスチャーでポインタ停止、その他のジェスチャーでそれに割り当てられた機能を即実行します。",
    "This assignment is used only while the gaze launcher is open.":
        "視線ランチャー起動中のみ、この割り当てを使用します。",
    "This assignment is used only while the gaze task switcher is open.":
        "視線タスクスイッチャー起動中のみ、この割り当てを使用します。",
    "This assignment is used only while the gaze clipboard history is open.":
        "視線クリップボード履歴起動中のみ、この割り当てを使用します。",
    "Set this when gestures other than the one you mean respond at the same time.":
        "目的のジェスチャー以外が同時に反応してしまう時に設定します。",
    "Weak gestures barely register; strong ones respond a lot.":
        "弱いジェスチャーではほとんど反応せず、強いと大きく反応します。",
    "Even a weak gesture responds strongly from the start.":
        "弱いジェスチャーでもはじめから大きく反応します。",
    "Larger values make the pointer move more smoothly, at the cost of more delay.":
        "大きくするとマウスポインタが滑らかに動きますが、その分遅延します。",
    "After the first click, keeping the gesture up for this long turns it into a held press (drag).":
        "1回クリックした後、指定時間ジェスチャーを続けると押しっぱなし(ドラッグ)に変わります。",
    "Exact match only. Wildcards (*, ?) can be used.":
        "完全一致のみ。ワイルドカード(*,?)使用可。",
    "Startup": "スタートアップ",
    "At sign-in (starts on the sign-in screen etc.)": "サインイン時(ログイン画面等で起動)",
    "After sign-in (starts on the desktop, UAC prompts etc.)": "サインイン後(デスクトップ、UAC画面等で起動)",
    "Applying... allow it if Windows asks for permission.": "設定中… 許可を求められたら「はい」を選んでください。",
    "Not changed: permission was not given.": "変更されませんでした: 許可されませんでした。",
    "Could not change the setting: %s": "設定を変更できませんでした: %s",
    "Face control on": "顔操作 オン",
    "Face control off": "顔操作 オフ",
    "Face not detected": "顔が見えません",
    "Manage profiles":
        "プロファイルを管理",
    "not a packaged build (dev run)":
        "開発用の実行です（パッケージ版ではありません）",
    "%s is missing from this build":
        "%s がこのビルドにありません",
    "installed in %s - it has to be under Program Files":
        "%s にインストールされています。Program Files 内である必要があります",
    "%s is not signed":
        "%s に署名がありません",
    "%s is not signed by a trusted certificate":
        "%s の署名が信頼されていません",
    "exit code %d":
        "終了コード %d",
    "Starts an ultra-light version just for signing in to Windows, meant only for typing with the on-screen keyboard. Only mouse operation, Gaze Jump and Gaze move can be used. The Gaze Jump and Gaze move animations are not drawn.":
        "スクリーンキーボードの入力のみを目的とした、Windowsサインイン専用の超軽量版を起動します。マウス操作、視線ジャンプ、視線移動のみ使用可。視線ジャンプ、視線移動のアニメーション描画は省略されます。",
    "This is the first launch, so the gaze calibration runs now. On the next screen, look at each green dot in turn.\nIf the gaze seems off, recalibrate often from the left menu.":
        "初回起動のため視線キャリブレーションを行います。次の画面で緑の点を順番に見つめて下さい。\n視線にずれを感じたら、こまめに左メニューより再キャリブレーションを行って下さい。",
    "Camera not found.\nIt is not connected, or another app is using it.":
        "カメラが見つかりません。\n接続されていないか、他アプリが使用中です。",
    "Jump to the nearest button or link": "直近のボタンやリンクにジャンプ",
    "If there is a button or link near where the jump lands, the pointer goes onto it. In some apps and sites this does not work, or picks the wrong thing.":
        "ジャンプ先の近くにボタンやリンクがあれば、そこにジャンプします。アプリやサイトによっては無効、または誤認識する事があります。",
    "Range (px)": "範囲 (px)",
    "Visual cue": "視覚的に通知",
    "When the jump lands on a button or link, its animation is coloured to tell you.":
        "ボタンやリンクにジャンプした時にアニメーションに色を付け通知します。",
    "Slider lower limit": "スライダ下限値",
    "Mainly used when the response curve is set to \"strong -> weak\".": "主に反応カーブを強→弱に設定した時に使用します。",
    "Disable on strong reaction": "強い反応時に無効化",
    "Keeps a gesture from misfiring when you laugh suddenly or react in some other way you didn't mean.":
        "不意に笑ってしまったりする事で発生するジェスチャーの暴発を抑制します。",
    "Search": "検索",
    "Added to pinned": "ピン留めに追加しました",
    "Removed from pinned": "ピン留めから外しました",
    "%d / %d  (%d items)": "%d / %d  (%d 件)",
    "Sensitivity": "検出感度",
    "About GazeFace": "GazeFaceについて",
    "GazeFace update": "GazeFace の更新",
    "GazeFace %s is available.": "GazeFace %s があります。",
    "While it updates, GazeFace closes for a moment and then starts again by itself.":
        "更新中は GazeFace が一度終了し、終わると自動で起動します。",
    "Update now": "今すぐ更新",
    "Later": "後で",
    "Preparing the update...": "更新を準備しています…",
    "Downloading the update... %d%%": "更新をダウンロードしています… %d%%",
    "Installing the update...": "更新をインストールしています…",
    "No newer version was found.": "新しいバージョンは見つかりませんでした。",
    "The update is installed. Restart GazeFace to use it.":
        "更新をインストールしました。GazeFace を再起動すると使えます。",
    "The update could not be started.": "更新を開始できませんでした。",
    "The update failed.": "更新に失敗しました。",
    "What's new in GazeFace %s": "GazeFace %s の更新内容",
    "GazeFace has been updated to %s.": "GazeFace を %s に更新しました。",
    "Suppresses chattering, where a click or other input is entered several times in a row without you meaning it. Recommended on.":
        "クリックなどの入力が意図せず連続して入力されてしまうチャタリングを抑制します。オン推奨。",
    "When an error occurs in the app, it tries to restart itself automatically so you are not left unable to operate it. If the settings are broken, it restarts with an earlier backup profile. Normally there is no need to change this setting.":
        "アプリにエラーが発生した時に、操作不能とならないように自動で再起動を試みます。設定が壊れていた場合、過去のバックアッププロファイルで再起動します。通常は設定を変える必要はありません。",
    "Disabled time (s)": "無効時間（秒）",
}

TRANSLATIONS = {
    "ja": JA,
}
