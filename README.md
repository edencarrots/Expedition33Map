# Expedition 33 Compass

Interactive map app for *Clair Obscur: Expedition 33* built with Streamlit and Folium.

The app loads a custom world map image, overlays points of interest from a CSV file, and lets you:

- search and filter POIs by category
- mark locations as completed
- focus the map on a selected POI
- plan simple proximity-based routes between two points

## Stack

- Python
- Streamlit
- Folium
- `streamlit-folium`
- Pandas
- NumPy

## Project Structure

```text
app.py                  Streamlit UI entrypoint
map_component.py        Folium map rendering
data_engine.py          Data loading, filtering, routing, state helpers
constants.py            App and map configuration
assets/map_bg.avif      Background map image
expedition33_map_coordinates.csv
tests/test_data_engine.py
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Then open the local Streamlit URL shown in the terminal.

## Tests

```bash
pytest tests/ -v
```

## Data

The map markers come from `expedition33_map_coordinates.csv`, which includes:

- `id`
- `name`
- `category`
- `x`
- `y`

The background image is loaded from `assets/map_bg.avif`.

## Notes

- The app uses `CRS.Simple` coordinates for the custom image overlay.
- A developer calibration panel is available when `DEV_MODE` is enabled in `constants.py`.
