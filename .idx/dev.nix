# Google Project IDX configuration
# Place this file at the root of your workspace as .idx/dev.nix

{ pkgs }: {
  channel = "stable-24.05";

  packages = [
    pkgs.python312
    pkgs.python312Packages.pip
  ];

  idx = {
    extensions = [
      "ms-python.python"
      "ms-python.vscode-pylance"
      "ms-toolsai.jupyter"
    ];

    previews = {
      enable = true;
      previews = {
        web = {
          command = [
            "streamlit"
            "run"
            "ui/app.py"
            "--server.port"
            "$PORT"
            "--server.address"
            "0.0.0.0"
            "--server.headless"
            "true"
          ];
          manager = "web";
        };
      };
    };

    workspace = {
      onCreate = {
        install-deps = "pip install -r requirements.txt";
      };
      onStart = {
        # nothing — use the Preview panel to launch Streamlit
      };
    };
  };
}
