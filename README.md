# Hardcopy

Take a photograph of a printed page and put it on paper.

A phone photo of a page is not a scan. It carries the shadow of the person holding the
phone, the tone of the paper, the dark surface behind the page, and usually a thumb.
Printing it directly spends toner on all four and gives back a grey rectangle.
`hardcopy` removes those, crops to what is actually printed, lays it out at a real dot
density, and sends it to an IPP printer.

## Install

```sh
pip install -e .
```

## Use

```sh
# clean the pages and write them out
hardcopy prepare IMG_0001.jpg IMG_0002.jpg --out-dir ./pages

# clean the pages and write one PDF
hardcopy pdf IMG_0001.jpg IMG_0002.jpg -o "Some Title.pdf" --title "Some Title"

# clean the pages and print them
hardcopy print IMG_0001.jpg IMG_0002.jpg --host printer.example.net

# check the printer accepts the job, print nothing
hardcopy print IMG_0001.jpg --host printer.example.net --validate-only
```

Options that apply to all three: `--dpi` (default 300), `--page letter|a4`, and
`--margin` in inches (default 0.3).

**There is no default printer address, and there must never be one.** `--host` is
required. Keep the address of your own printer in your own notes.

## How it works

Four steps, in order:

| Step | Function | What it does |
| --- | --- | --- |
| 1 | `flatten` | Divides the image by its own blurred self, so illumination goes away. |
| 2 | `ink_bbox` | Finds what is actually printed and crops to it. |
| 3 | `despeckle` | Deletes solid masses at the crop edge that are page, not ink. |
| 4 | `fit_page` | Lays the result on a page-size canvas at the chosen dot density. |

Step 1 is a division, not a threshold. A threshold picks one brightness for the whole
image, and a photographed page does not have one: the lit corner and the shadowed
corner differ by more than ink differs from paper. Dividing by a local background
removes the gradient first, so one stretch then works everywhere. A threshold also
destroys thin marks, and a rule three pixels wide is exactly what a page of music or a
table is made of.

## Two things about printers

* **Output is single-sided.** Many printers accept only one document per job. A second
  `Send-Document` returns `0x0509` and aborts the whole job, so N pages goes out as N
  separate `Print-Job` calls. Duplex needs every page inside one multi-page raster,
  which this tool does not build. For sheet music this is what you want anyway: both
  pages sit on the stand together.
* **A finished job disappears fast.** Job history can clear within seconds. `hardcopy
  print` therefore confirms by watching the queue drain and the printer return to idle,
  and it fails loudly if the printer reports any state reason -- out of paper, a jam, an
  open cover -- so a job that never reached paper is never reported as printed.

## Develop

```sh
pip install -e ".[dev]"
ruff check .
pytest -q
```

Every test fixture is drawn in code. No photograph is committed, so a failing test names
the property that broke rather than the picture that changed.
