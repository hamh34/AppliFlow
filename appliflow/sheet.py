"""Google Sheets read/write for the tracker worksheet."""

from __future__ import annotations

from .openings import COLUMNS as OPENING_COLUMNS
from .openings import Opening
from .records import COLUMNS, Application

HEADER_ROW = 1
FIRST_DATA_ROW = 2

_LAST_COLUMN = chr(ord("A") + len(COLUMNS) - 1)
_OPENINGS_LAST_COLUMN = chr(ord("A") + len(OPENING_COLUMNS) - 1)
_OPENINGS_URL_INDEX = OPENING_COLUMNS.index("URL")


class SheetError(RuntimeError):
    pass


class Tracker:
    def __init__(self, creds, spreadsheet_id: str, worksheet: str):
        from googleapiclient.discovery import build

        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self._values = self._service.spreadsheets().values()
        self.spreadsheet_id = spreadsheet_id
        self.worksheet = worksheet

    def _range(self, cells: str) -> str:
        # Quote the tab name so worksheets with spaces resolve correctly.
        return f"'{self.worksheet}'!{cells}"

    def ensure_header(self) -> bool:
        """Write the header row if it is missing. Returns True if it wrote one."""
        existing = (
            self._values.get(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(f"A{HEADER_ROW}:{_LAST_COLUMN}{HEADER_ROW}"),
            )
            .execute()
            .get("values", [])
        )
        if existing and existing[0] and any(cell.strip() for cell in existing[0]):
            return False

        self._values.update(
            spreadsheetId=self.spreadsheet_id,
            range=self._range(f"A{HEADER_ROW}"),
            valueInputOption="RAW",
            body={"values": [COLUMNS]},
        ).execute()
        return True

    def read_all(self) -> list[Application]:
        rows = (
            self._values.get(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(f"A{FIRST_DATA_ROW}:{_LAST_COLUMN}"),
            )
            .execute()
            .get("values", [])
        )

        applications = []
        for offset, row in enumerate(rows):
            if not any((cell or "").strip() for cell in row):
                continue
            applications.append(Application.from_row(row, row=FIRST_DATA_ROW + offset))
        return applications

    def append(self, applications: list[Application]) -> None:
        if not applications:
            return
        self._values.append(
            spreadsheetId=self.spreadsheet_id,
            range=self._range(f"A{FIRST_DATA_ROW}"),
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [app.to_row() for app in applications]},
        ).execute()

    def update(self, applications: list[Application]) -> None:
        """Rewrite rows in place. Records without a row number are skipped."""
        data = [
            {
                "range": self._range(f"A{app.row}:{_LAST_COLUMN}{app.row}"),
                "values": [app.to_row()],
            }
            for app in applications
            if app.row
        ]
        if not data:
            return
        self._values.batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()


class OpeningsSheet:
    """Writes discovered openings to their own tab, separate from applications."""

    def __init__(self, creds, spreadsheet_id: str, worksheet: str):
        from googleapiclient.discovery import build

        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self._values = self._service.spreadsheets().values()
        self.spreadsheet_id = spreadsheet_id
        self.worksheet = worksheet

    def _range(self, cells: str) -> str:
        return f"'{self.worksheet}'!{cells}"

    def ensure_tab(self) -> None:
        """Create the worksheet if the spreadsheet does not have it yet."""
        metadata = self._service.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id
        ).execute()
        titles = {
            sheet["properties"]["title"] for sheet in metadata.get("sheets", [])
        }
        if self.worksheet in titles:
            return
        self._service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": self.worksheet}}}]},
        ).execute()

    def ensure_header(self) -> bool:
        existing = (
            self._values.get(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(f"A{HEADER_ROW}:{_OPENINGS_LAST_COLUMN}{HEADER_ROW}"),
            )
            .execute()
            .get("values", [])
        )
        if existing and existing[0] and any(cell.strip() for cell in existing[0]):
            return False
        self._values.update(
            spreadsheetId=self.spreadsheet_id,
            range=self._range(f"A{HEADER_ROW}"),
            valueInputOption="RAW",
            body={"values": [OPENING_COLUMNS]},
        ).execute()
        return True

    def known_urls(self) -> set[str]:
        """URLs already on the sheet, so repeat runs only add genuinely new rows."""
        rows = (
            self._values.get(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(f"A{FIRST_DATA_ROW}:{_OPENINGS_LAST_COLUMN}"),
            )
            .execute()
            .get("values", [])
        )
        urls = set()
        for row in rows:
            if len(row) > _OPENINGS_URL_INDEX:
                value = (row[_OPENINGS_URL_INDEX] or "").strip()
                if value:
                    urls.add(value)
        return urls

    def append(self, openings: list[Opening]) -> None:
        if not openings:
            return
        self._values.append(
            spreadsheetId=self.spreadsheet_id,
            range=self._range(f"A{FIRST_DATA_ROW}"),
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [opening.to_row() for opening in openings]},
        ).execute()
