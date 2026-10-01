import json

CONFIG_PATH = "/data/solar-fortress/config/controller.json"

with open(CONFIG_PATH, "r") as config_file:
    config = json.load(config_file)

thresholds = config["soc_thresholds"]
qualification = config["qualification_seconds"]

print("Solar Fortress configuration loaded successfully.")
print()
print(f"Grid pass-through threshold: {thresholds['grid_pass_through']}%")
print(f"Grid charging threshold:     {thresholds['grid_charge']}%")
print(f"Battery return threshold:    {thresholds['battery_return']}%")
print()
print(f"Grid-charge qualification:   {qualification['grid_charge']} seconds")
print(f"Stop-charge qualification:   {qualification['stop_grid_charge']} seconds")
