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
  };

  outputs = { self, nixpkgs, home-manager, broccoli-comms }@inputs:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      homeConfigurations.codex = home-manager.lib.homeManagerConfiguration {
        inherit pkgs;
        modules = [
          broccoli-comms.homeManagerModules.default
          ./home.nix
        ];
      };
    };
}
