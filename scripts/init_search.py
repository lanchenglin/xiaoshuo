"""Create local-only SearXNG settings once; do not overwrite an existing secret."""
from pathlib import Path
import secrets


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / 'data' / 'searxng'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'settings.yml'
    if path.exists():
        print('SearXNG settings already exist; left unchanged.')
        return
    text = ('use_default_settings: true\n'
            'search:\n  formats:\n    - html\n    - json\n'
            'server:\n  bind_address: "0.0.0.0"\n  port: 8080\n'
            '  limiter: false\n  secret_key: "' + secrets.token_hex(32) + '"\n')
    with path.open('x', encoding='utf-8') as handle:
        handle.write(text)
    print('Created data/searxng/settings.yml. Secret is not printed. Keep port localhost-only.')


if __name__ == '__main__':
    main()
