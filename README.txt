
O que este patch faz
- Transforma os botões "Editar", "Excluir" e "Salvar/Adicionar" em **ícones** com **tooltips**.
- Deixa a sidebar **mobile-friendly**: no celular ela vira sobreposição (abre/fecha pelo botão).
- Inclui CSS e JS necessários para responsividade e tooltips.
- Tabelas com `.table-responsive` para caber no celular.

Como aplicar
1) Copie o conteúdo deste zip por cima do seu projeto (mantém a mesma estrutura).
   - `templates/admin/categories.html`
   - `templates/admin/rates.html`
   - `templates/admin/_modal_hook.html`
   - `static/css/custom.css` (apenas o trecho novo; se já tiver o seu, mescle).
2) Garanta que o seu `templates/base.html`:
   - Carrega **Bootstrap JS** (bundle) antes do `extra_js`.
   - Tenha o bloco: `{% block extra_js %}{% endblock %}`.
   - Já exista o botão com id `sidebarToggle` (o seu layout tem).
3) Reinicie a app e faça **Ctrl+F5**.

Observações
- Os ícones usam **Bootstrap Icons** (já estão referenciados no seu base.html).
- Para qualquer botão icônico, use a classe `btn-icon` e adicione `title="..."` + `data-bs-toggle="tooltip"`.
