# mihomo-py-web

Optional static subscription portal and zashboard resources for [mihomo-py](https://pypi.org/project/mihomo-py/).

```bash
pip install 'mihomo-py[web]'
```

Ships pinned zashboard v3.29.1 (`dist-no-fonts.zip`), MIT license, upstream dependency
notices and local integration script. No GitHub/CDN downloads during installation
or startup. The main client serves the panel at its management port's `/ui/` path.

The React subscription portal is bundled in `portal/`; its sources are in `frontend/`,
and dependency licenses are in `portal/THIRD_PARTY_NOTICES.txt`.

Source and upstream attribution are included in `_vendor/NOTICE.txt` and `manifest.json`.
