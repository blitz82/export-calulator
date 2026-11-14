from pathlib import Path

from openpyxl import load_workbook

from epms.submission import clear_example_rows


def main():
    template = next(Path("samples").glob("2027*.xlsx"))
    book = load_workbook(template, keep_links=False)
    clear_example_rows(book)
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.comment:
                    cell.comment = None
    destination = Path("build/template/2027-공식양식.xlsx")
    destination.parent.mkdir(parents=True, exist_ok=True)
    book.save(destination)


if __name__ == "__main__":
    main()
