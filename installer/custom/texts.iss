; 自分で書くテキスト（GazeFace.iss から #include）。
; builder_gui.py が書き出す。手で編集する場合:
; %n = 改行。1項目1行で書く（行の途中で改行しない）。
; DoneFull / DoneStandard を空にすると、Inno Setup 標準の完了メッセージになる。

[CustomMessages]
; 版選択ページ・起動の設定ページ：各選択肢の下の説明
en.FullDesc=Almost everything can be done without help.%nGazeFace also works in administrator windows, User Account Control prompts and on the sign-in screen, and runs more reliably.%nCaution: this needs UIAccess, a special permission reserved for assistive software. So setup creates a certificate valid only on this PC, adds it to "Trusted Root Certification Authorities", and signs GazeFace and its own mouse driver with it. The private key is deleted right after signing, but this still changes how your PC decides what to trust. Choose this only if you understand the risk and accept it as your own responsibility. Always get the installer from the official releases.
ja.FullDesc=ほぼすべての操作を介助なしで行えます。%n管理者として動くウィンドウ、ユーザー アカウント制御の確認画面、サインイン画面でも操作でき、より安定して動作します。%n注意: UIAccess という支援ソフト専用の特別な権限を必要とする為、この PC だけで通用する証明書を作って「信頼されたルート証明機関」に登録し、GazeFace と専用マウス ドライバーに署名します。秘密鍵は署名後すぐに削除しますが、PC の信頼の仕組みに手を加える変更です。リスクを理解したうえで、自己責任で選んでください。インストーラーは必ず公式リリースから入手してください。
en.StandardDesc=GazeFace running with normal permissions. No certificate is added, nothing is signed, and the mouse driver is not installed.%nIt cannot operate windows running as administrator (Task Manager, installers, etc.), User Account Control prompts, or the sign-in and lock screens. While one of these is in front, the pointer or clicks stop working, so help from family or a caregiver will be needed.
ja.StandardDesc=通常の権限で動く GazeFace です。証明書の登録や署名、専用マウスドライバーのインストールは行いません。%n管理者として動くウィンドウ（タスク マネージャー、インストーラーなど）、ユーザー アカウント制御の確認画面、サインイン画面やロック画面は操作できません。これらが前面に出るとポインターやクリックが効かなくなるため、家族や介助者の手助けが必要になります。
en.LogonDesc=Starts a super-light edition made only for the Windows sign-in screen, for typing with the on-screen keyboard. Only mouse control, gaze jump and gaze move are available, and their animations are not drawn.
ja.LogonDesc=スクリーンキーボードの入力のみを目的とした、Windowsサインイン専用の超軽量版を起動します。マウス操作、視線ジャンプ、視線移動のみ使用可。視線ジャンプ、視線移動のアニメーション描画は省略されます。
en.AfterSigninDesc=
ja.AfterSigninDesc=

; 完了ページの注意書き（版ごと）
en.DoneFull=
ja.DoneFull=
en.DoneStandard=
ja.DoneStandard=
