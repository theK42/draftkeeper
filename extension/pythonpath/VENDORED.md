Vendored, pure-Python wheels (py3-none-any):
- dulwich 0.24.10
- typing_extensions 4.16.0 (dulwich needs it on Python < 3.12)

Local patch (reapply when updating dulwich):
- dulwich/pack.py: `except ModuleNotFoundError:` -> `except ImportError:` around the optional
  `cdifflib` import. LibreOffice's uno import hook re-raises missing modules as plain ImportError.

Licenses: dulwich/COPYING (Apache-2.0 or GPL-2.0-or-later), typing_extensions.LICENSE (PSF-2.0).
