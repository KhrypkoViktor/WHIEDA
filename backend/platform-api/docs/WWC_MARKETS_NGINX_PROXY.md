# WWC Markets API — nginx reverse proxy (ops reference)

Public site `wwc.best` should proxy **read-only** markets endpoints to Platform API.
Do **not** expose sync CLI or admin routes. Existing `POST /api/lead` stays on its current upstream.

## Upstream

Platform API base (staging example):

```text
http://127.0.0.1:8080
```

Adjust host/port for your deployment.

## Location blocks (wwc.best server)

Add inside the `wwc.best` `server { }` block:

```nginx
location = /api/site-context {
    proxy_pass http://platform_api/v1/site-context;
    include proxy_params.conf;
}

location = /api/catalog-prices {
    proxy_pass http://platform_api/v1/catalog-prices;
    include proxy_params.conf;
}

location = /api/service-centers {
    proxy_pass http://platform_api/v1/service-centers;
    include proxy_params.conf;
}

location = /api/service-center-cities {
    proxy_pass http://platform_api/v1/service-center-cities;
    include proxy_params.conf;
}
```

`proxy_params.conf` should forward `Host`, `X-Forwarded-For`, and `X-Request-Id` for tenant resolution.

## Notes

- Core paths `/v1/site-context`, `/v1/catalog-prices`, etc. remain on Platform API directly.
- Frontend flag `market_centers_v1` stays **off** until owner enables it; proxy can be deployed beforehand.
- No Google credentials or Sheet IDs in nginx config.
