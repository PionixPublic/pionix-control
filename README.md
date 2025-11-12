# Control application for Belaybox and MicroMegaWattCharger
Expects a list of configs at /mnt/user_data/everest_configs.yaml in the format shown in the everest_configs.yaml in this repo.
Expects a systemd service called everest-control.service in /etc/systemd/system/everest-control.service (for EVerest)
Expects a systemd service called pionix-control.service in /etc/systemd/system/pionix-control.service (for this tool)
Writes an environment file to /mnt/user_data/everest_environment.env that is used by the systemd service
