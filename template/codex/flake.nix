{
  description = "Codex Agent Home Manager Configuration";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    broccoli-comms = {
      url = "github:tanmayv/broccoli-comms";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pi-nix = {
      url = "github:lukasl-dev/pi.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, broccoli-comms, pi-nix }@inputs:
    let
      setup = import ./setup.nix;
      system = setup.system;
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      homeConfigurations.codex = home-manager.lib.homeManagerConfiguration {
        inherit pkgs;
        extraSpecialArgs = { inherit inputs setup; };
        modules = [
          broccoli-comms.homeManagerModules.default
          ./home.nix
          ({
            lib,
            ...
          }:
            let
              bc = setup.broccoli;
              homeDir = setup.homeDirectory;
              cacheDir = "${homeDir}/.cache";
              agentTrackerCfg = bc."agent-tracker" or {};
            in {
              services.broccoli-comms = {
                enable = lib.mkDefault (if bc ? "enable-agent-tracker" then bc."enable-agent-tracker" else bc.enable);

                # Apply tracker/registry and comms options from setup.nix
                config = {
                  paths = {
                    runtimeDir = "${cacheDir}/broccoli-comms/runtime";
                    cacheDir = "${cacheDir}/broccoli-comms";
                    configDir = "${homeDir}/.config/broccoli-comms";
                    agentRootDir = lib.mkDefault bc.agentRootDir;
                    tmuxSocket = null;
                    permissionDetectionConfig = null;
                  };

                  tracker = {
                    registries = lib.mkDefault (agentTrackerCfg.registries or [ ]);
                  };

                  registry = {
                    remoteRunEnabled = lib.mkDefault (bc.remoteRunEnabled or false);
                    authEnabled = lib.mkDefault (agentTrackerCfg."registry-auth" or bc.authEnabled or false);
                  };
                };
              };
            })
        ];
      };
    };
}
