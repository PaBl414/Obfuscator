# Contributing

## Development setup

The runtime uses only the Python standard library. Python 3.10 or newer is
required.

## Validation

Run the complete test suite before opening a pull request:

```powershell
python -m unittest discover -s tests -v
```

Changes to a transformation stage should include focused tests for both its
transformed output and any safety cases that must remain unchanged.
