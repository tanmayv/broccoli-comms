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
        ];
      };
    };
}
