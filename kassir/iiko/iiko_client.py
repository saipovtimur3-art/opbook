"""Минимальный клиент iikoServer API (resto API) для OLAP-отчётов по продажам.

Только стандартная библиотека Python, без внешних зависимостей.
Документация iiko: https://ru.iiko.help/articles/#!api-documentations/iikoserver-api
"""
import hashlib
import json
import ssl
import urllib.parse
import urllib.request


class IikoError(Exception):
    pass


class IikoServer:
    """Использовать через `with IikoServer(...) as api:` — так токен всегда
    освобождается (каждый открытый токен занимает лицензию API на сервере)."""

    def __init__(self, url, login, password, timeout=60, verify_ssl=True):
        self.base = url.rstrip("/")
        if not self.base.endswith("/resto"):
            self.base += "/resto"
        self.login = login
        self.password = password
        self.timeout = timeout
        self.ctx = None if verify_ssl else ssl._create_unverified_context()
        self.token = None

    # ---------- HTTP ----------
    def _req(self, method, path, params=None, body=None):
        params = dict(params or {})
        if self.token and path != "/api/auth":
            params["key"] = self.token
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        data, headers = None, {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise IikoError(f"{method} {path}: HTTP {e.code} {e.read().decode('utf-8', 'ignore')[:500]}") from None
        except urllib.error.URLError as e:
            raise IikoError(f"Нет связи с {self.base}: {e.reason}") from None

    # ---------- сессия ----------
    def auth(self):
        pass_hash = hashlib.sha1(self.password.encode("utf-8")).hexdigest()
        self.token = self._req("GET", "/api/auth", {"login": self.login, "pass": pass_hash}).strip()
        return self.token

    def logout(self):
        if self.token:
            try:
                self._req("GET", "/api/logout")
            except IikoError:
                pass
            self.token = None

    def __enter__(self):
        self.auth()
        return self

    def __exit__(self, *exc):
        self.logout()

    # ---------- OLAP ----------
    def olap_columns(self, report_type="SALES"):
        """Все поля OLAP-отчёта: {id: {name, type, groupingAllowed, ...}}."""
        return json.loads(self._req("GET", "/api/v2/reports/olap/columns", {"reportType": report_type}))

    def olap(self, group_by, aggregates, filters, report_type="SALES"):
        body = {
            "reportType": report_type,
            "buildSummary": False,
            "groupByRowFields": group_by,
            "groupByColFields": [],
            "aggregateFields": aggregates,
            "filters": filters,
        }
        return json.loads(self._req("POST", "/api/v2/reports/olap", body=body)).get("data", [])


def date_filter(date_from, date_to):
    """Период [date_from, date_to) в формате YYYY-MM-DD (учётный день iiko)."""
    return {
        "filterType": "DateRange",
        "periodType": "CUSTOM",
        "from": date_from,
        "to": date_to,
        "includeLow": True,
        "includeHigh": False,
    }


def include(values):
    return {"filterType": "IncludeValues", "values": list(values)}
