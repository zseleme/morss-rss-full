# morss-rss-full

Fork do [morss](https://github.com/pictuga/morss) com melhorias para uso pessoal self-hosted no Raspberry Pi.

[![CI](https://github.com/zseleme/morss-rss-full/actions/workflows/default.yml/badge.svg)](https://github.com/zseleme/morss-rss-full/actions)
[![GNU AGPLv3](https://img.shields.io/static/v1?label=license&message=AGPLv3&color=blue)](LICENSE)

## O que é

Proxy de RSS que busca o texto completo dos artigos e os embute no feed, permitindo leitura integral em qualquer leitor de RSS sem acessar o site original.

## Modificações em relação ao upstream

- **Python 3.13**: corrige remoção do módulo `cgi` e `cgitb`
- **Extratores customizados**: suporte específico para sites com proteção anti-bot
  - `br.investing.com` — usa versão AMP + `curl_cffi` (Chrome TLS fingerprint)
  - `moneytimes.com.br` — seletores específicos do layout
- **Página de status** (`/status`) com uptime, cache, últimas requisições e IP do cliente
- **Página de erro** limpa
- **Interface** redesenhada

## Deploy (Raspberry Pi + Docker + Caddy)

```bash
git clone https://github.com/zseleme/morss-rss-full /opt/docker/morss
cd /opt/docker/morss
docker compose up -d --build
```

### compose.yml

```yaml
networks:
  proxy:
    external: true

services:
  morss:
    build: .
    image: morss:local
    container_name: morss
    restart: unless-stopped
    environment:
      - PORT=8000
      - MAX_ITEM=10
      - MAX_TIME=30
      - LIM_ITEM=20
    ports:
      - "127.0.0.1:8001:8000"
    volumes:
      - morss_cache:/var/cache/morss
      - /opt/docker/morss/www:/usr/share/morss/www:ro
    networks:
      - proxy

volumes:
  morss_cache:
```

### Caddyfile

```
rss.example.com {
  reverse_proxy morss:8000
}
```

## Uso

Acesse `https://rss.example.com/` e cole a URL do feed, ou use diretamente:

```
https://rss.example.com/br.investing.com/rss/news_285.rss
https://rss.example.com/www.moneytimes.com.br/feed/
```

### Forçar atualização (ignorar cache)

```
https://rss.example.com/:force/br.investing.com/rss/news_285.rss
```

### Status

```
https://rss.example.com/status
```

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `MAX_ITEM` | 5 | Máximo de artigos buscados por requisição |
| `MAX_TIME` | 2 | Tempo máximo (s) de busca de artigos |
| `LIM_ITEM` | 10 | Máximo de itens no feed (demais são descartados) |
| `LIM_TIME` | — | Tempo máximo total (s) de processamento |
| `CACHE` | memória | Backend de cache: `redis` ou `diskcache` |
| `CACHE_SIZE` | 1000 | Número máximo de itens no cache |

## Adicionar extrator customizado

Para sites que bloqueiam o crawler padrão, adicione em `morss/morss.py`:

```python
@custom_extractor('exemplo.com.br')
def extract_exemplo(url):
    from curl_cffi import requests as r
    from bs4 import BeautifulSoup
    resp = r.get(url, impersonate='chrome120', timeout=15)
    soup = BeautifulSoup(resp.content, 'lxml')
    el = soup.select_one('div.article-content')
    return str(el) if el else None
```

## Licença

[GNU AGPLv3](LICENSE) — fork do [morss](https://github.com/pictuga/morss) por pictuga.
