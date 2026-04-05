Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\Users\USERNAME\AppData\Roaming\com.hermes.companion\daemon\bin"
shell.Run """C:\Users\USERNAME\AppData\Roaming\com.hermes.companion\daemon\bin\hermes-node-daemon.exe"" --config ""C:\Users\USERNAME\AppData\Roaming\com.hermes.companion\daemon\daemon-config.json""", 0, False
