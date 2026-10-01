import dbus
import os


def get_bus():
    if "DBUS_SESSION_BUS_ADDRESS" in os.environ:
        return dbus.SessionBus()
    return dbus.SystemBus()


def read_value(bus, service, path):
    try:
        obj = bus.get_object(service, path, introspect=False)
        item = dbus.Interface(obj, "com.victronenergy.BusItem")
        return item.GetValue()
    except Exception:
        return None


bus = get_bus()

services = sorted(
    name
    for name in bus.list_names()
    if name.startswith("com.victronenergy.")
)

print("Solar Fortress Victron D-Bus Probe")
print("=" * 50)
print()

if not services:
    print("No Victron services found.")
    raise SystemExit(0)

paths = [
    "/ProductName",
    "/CustomName",
    "/Mgmt/Connection",
    "/DeviceInstance",
    "/Connected",
    "/FirmwareVersion",
    "/Soc",
    "/State",
    "/Mode",
    "/Ac/ActiveIn/Connected",
    "/Ac/ActiveIn/ActiveInput",
    "/Dc/0/Voltage",
    "/Dc/0/Current",
]

for service in services:
    print(service)

    found_any = False

    for path in paths:
        value = read_value(bus, service, path)

        if value is not None:
            found_any = True
            print(f"  {path}: {value}")

    if not found_any:
        print("  (none of the probe paths available)")

    print()
