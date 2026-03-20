import pandas as pd
df = pd.read_csv('ITSM_data.csv', nrows=2)
import json
with open('cols.json', 'w') as f:
    json.dump({"columns": df.columns.tolist()}, f)
