# PC10 Wave 1

Queue:

- Manifest: `data_shards/recovery_remaining_after_windows_smoke_20260525_500/manifest.json`
- Batch range: `1149-1178`
- Workers: `1`

Config files:

- Tracked base config: `configs/windows/config.windows.pc10.1worker.wave1.email.json`
- Local overlay: `configs/windows/config.windows.pc10.1worker.wave1.email.local.json`

One-time setup on Windows:

1. Copy `config.windows.pc10.1worker.wave1.email.local.example.json` to `config.windows.pc10.1worker.wave1.email.local.json`.
2. Fill the SMTP settings in the local file.
3. Keep `browser.user_data_dir` on a persistent local path such as `C:/vimeo_profiles/pc10_account_a`.

Preflight:

```powershell
.\venv\Scripts\python.exe .\scripts\preflight_parse.py --config .\configs\windows\config.windows.pc10.1worker.wave1.email.local.json --manifest .\data_shards\recovery_remaining_after_windows_smoke_20260525_500\manifest.json --min-local-free-gb 35
```

Launch:

```powershell
.\venv\Scripts\python.exe .\run_assigned_shards.py --config .\configs\windows\config.windows.pc10.1worker.wave1.email.local.json --manifest .\data_shards\recovery_remaining_after_windows_smoke_20260525_500\manifest.json --workers 1 --batch-start 1149 --batch-end 1178
```

Tail coordinator log:

```powershell
Get-Content .\output\runs\pc10\windows-1worker-wave1-email\workers\coordinator.log -Wait
```
