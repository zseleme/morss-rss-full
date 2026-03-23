# rss-full

> Proxy RSS que busca e embute o texto completo dos artigos no feed, com suporte a extratores customizados para sites com proteção anti-bot.

## Funcionalidades

- Conversão de feeds RSS parciais em feeds com texto completo dos artigos
- Extratores customizados para sites com proteção anti-bot:
  - `br.investing.com` — utiliza versão AMP com spoofing de TLS fingerprint via `curl_cffi`
  - `moneytimes.com.br` — seletores CSS específicos para o layout do site
- Compatibilidade com Python 3.13 (corrige remoção dos módulos `cgi` e `cgitb` do upstream)
- Página de status (`/status`) com uptime, estatísticas de cache, últimas requisições e IP do cliente
- Interface web redesenhada para inserção de feeds
- Página de erro limpa e informativa
- Cache configurável (memória, Redis ou disco)
- Implantação via Docker com suporte a reverse proxy Caddy
- Healthcheck integrado no container

## Tecnologias

- Python 3.13
- Flask (framework web)
- curl_cffi (spoofing de TLS fingerprint para sites protegidos)
- lxml / BeautifulSoup (parsing HTML)
- Docker + Docker Compose (conteinerização)
- Alpine Linux (imagem base)
- Caddy (reverse proxy, gerenciado externamente)

## Pré-requisitos

- Docker e Docker Compose instalados no host
- Rede Docker externa chamada `proxy` (para integração com Caddy)
- Caddy configurado no host como reverse proxy (opcional, mas recomendado para produção)

## Instalação / Deploy

### 1. Clonar o repositório

```bash
git clone https://github.com/zseleme/morss-rss-full /opt/docker/morss
cd /opt/docker/morss
```

### 2. Criar o arquivo `compose.yml`

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

### 3. Subir o container

```bash
docker compose up -d --build
```

### 4. Configurar o Caddy (Caddyfile)

```
rss.example.com {
  reverse_proxy morss:8000
}
```

### 5. Executar localmente sem Docker

```bash
pip install -r requirements.txt
python -m flask run
```

## Uso

Acesse `https://rss.example.com/` e cole a URL do feed RSS desejado, ou use diretamente na URL:

```
https://rss.example.com/br.investing.com/rss/news_285.rss
https://rss.example.com/www.moneytimes.com.br/feed/
```

### Forçar atualização (ignorar cache)

```
https://rss.example.com/:force/br.investing.com/rss/news_285.rss
```

### Verificar status do serviço

```
https://rss.example.com/status
```

### Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `MAX_ITEM` | 5 | Máximo de artigos buscados por requisição |
| `MAX_TIME` | 2 | Tempo máximo (s) de busca de artigos |
| `LIM_ITEM` | 10 | Máximo de itens no feed (demais são descartados) |
| `LIM_TIME` | — | Tempo máximo total (s) de processamento |
| `CACHE` | memória | Backend de cache: `redis` ou `diskcache` |
| `CACHE_SIZE` | 1000 | Número máximo de itens no cache |

### Adicionar extrator customizado

Para sites que bloqueiam o crawler padrão, adicione um extrator em `morss/morss.py` seguindo o padrão:

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

### Ver logs

```bash
docker compose logs -f
```

## Arquitetura

```
rss-full/
├── morss/              # Código-fonte principal (fork do morss)
│   └── morss.py        # Lógica central, extratores customizados
├── www/                # Arquivos estáticos da interface web
├── Dockerfile          # Build Alpine Linux com dependências Python
├── morss-helper        # Script de entrypoint e healthcheck
├── main.py             # Entry point Flask
└── setup.py            # Definição do pacote Python
```

O fluxo de uma requisição é:

1. O cliente envia a URL do feed para o proxy
2. O proxy baixa o feed RSS original e itera sobre os itens
3. Para cada item, busca o conteúdo completo da página (com extrator padrão ou customizado)
4. O feed enriquecido com o texto completo é retornado ao cliente em formato RSS 2.0

O cache (em memória por padrão) evita requisições repetidas ao site original. O endpoint `/status` expõe métricas de uptime e cache em tempo real — os dados são voláteis e reiniciam junto com o container.

## Licença

[GNU AGPLv3](LICENSE) — fork do [morss](https://github.com/pictuga/morss) por pictuga.
