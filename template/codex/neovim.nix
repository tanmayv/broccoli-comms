{ config, pkgs, ... }: {
  programs.neovim = {
    enable = true;
    defaultEditor = true;
    viAlias = true;
    vimAlias = true;

    # Basic, clean Neovim configuration for development
    extraConfig = ''
      " Enable line numbers
      set number
      set relativenumber

      " Tab and indentation settings
      set expandtab
      set shiftwidth=2
      set tabstop=2
      set smartindent

      " Enable mouse support
      set mouse=a

      " Highlight search results
      set hlsearch
      set incsearch

      " Fast split navigation
      nnoremap <C-J> <C-W><C-J>
      nnoremap <C-K> <C-W><C-K>
      nnoremap <C-L> <C-W><C-L>
      nnoremap <C-H> <C-W><C-H>

      " Use system clipboard if available
      set clipboard+=unnamedplus
    '';
  };
}
