# Verification report

Exact commands run and concise results, grouped by task. Newest task last.
Results here are observations from the stated environment on the stated date; they
are not claims about other environments.

## T00 — Workspace and dependency baseline (2026-11-03, cloud sandbox, Ubuntu 24.04.5)

Workspace inspection:

```text
$ git status
On branch claude/fervent-bell-od7lan
No commits yet
$ ls -la            # only .git/ present
```

Result: empty repository; no existing user or planning files to preserve.

Runtimes:

```text
$ uv --version                     -> uv 0.11.32
$ uv python install 3.12.13        -> Installed Python 3.12.13
$ /usr/bin/python3.12 --version    -> Python 3.12.3 (system, not used for the lock)
$ node --version                   -> v22.23.4
$ npm --version                    -> 10.9.9
$ docker --version                 -> Docker version 29.9.2 (Compose plugin v5.6.1)
$ docker info                      -> daemon not running initially; started `dockerd`, then Server Version 29.9.2
$ docker pull postgres:17          -> pulled
$ docker run --rm postgres:17 postgres --version -> postgres (PostgreSQL) 17.11
$ psql --version                   -> psql (PostgreSQL) 16.15 (host client only)
```

Network reachability (single bounded request per host, no retries):

```text
https://pypi.org/simple/pydantic/                              -> 200
https://registry.npmjs.org/astro                               -> 200
https://eprint.iacr.org/oai?verb=Identify                      -> proxy CONNECT 403 (environment policy)
https://oaipmh.arxiv.org/oai?verb=Identify                     -> proxy CONNECT 403 (environment policy)
https://export.arxiv.org/api/query?search_query=cat:cs.CR&max_results=1 -> proxy CONNECT 403
https://dblp.org/xml/release/                                  -> proxy CONNECT 403
https://api.crossref.org/works?rows=1                          -> proxy CONNECT 403
```

Interpretation: the 403s come from this sandbox's egress policy, not from the
providers. They say nothing about whether the providers would accept our requests.
No live provider response has been observed in this environment.
