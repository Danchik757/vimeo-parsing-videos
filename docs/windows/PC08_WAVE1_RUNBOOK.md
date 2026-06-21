# PC08 Wave 1

Queue:

- Manifest: `data_shards/recovery_remaining_after_windows_smoke_20260525_500/manifest.json`
- Batch range: `1179-1208`
- Workers: `1`

Config files:

- Tracked base config: `configs/windows/config.windows.pc08.1worker.wave1.email.json`
- Local overlay: `configs/windows/config.windows.pc08.1worker.wave1.email.local.json`

One-time setup on Windows:

1. Copy `config.windows.pc08.1worker.wave1.email.local.example.json` to `config.windows.pc08.1worker.wave1.email.local.json`.
2. Fill the second Vimeo account credentials in `vimeo_login`.
3. Fill a second API keyset in `vimeo_api`.
4. Fill the SMTP settings in `email`.
5. Keep `browser.user_data_dir` on a persistent local path such as `C:/vimeo_profiles/pc08_account_b`.

Preflight:

```powershell
.\venv\Scripts\python.exe .\scripts\preflight_parse.py --config .\configs\windows\config.windows.pc08.1worker.wave1.email.local.json --manifest .\data_shards\recovery_remaining_after_windows_smoke_20260525_500\manifest.json --min-local-free-gb 35
```

Launch:

```powershell
.\venv\Scripts\python.exe .\run_assigned_shards.py --config .\configs\windows\config.windows.pc08.1worker.wave1.email.local.json --manifest .\data_shards\recovery_remaining_after_windows_smoke_20260525_500\manifest.json --workers 1 --batch-start 1179 --batch-end 1208
```

Tail coordinator log:

```powershell
Get-Content .\output\runs\pc08\windows-1worker-wave1-email\workers\coordinator.log -Wait
```
