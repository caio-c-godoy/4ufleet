PATCH (Correção):
- Ajusta o form de criação de Categoria para POSTAR NA MESMA ROTA (evita erro do endpoint).
- Remove dependência de 'rates_map' no template; faz lookup de tarifa por categoria diretamente no Jinja.
- Mantém botões "Editar" abrindo modal via fetch.
- Inclui partial compartilhado do modal + JS.

Como aplicar:
1) Extraia este zip por cima do seu projeto.
2) Copie o conteúdo de app/admin/routes_modal_snippet.py para o FINAL de app/admin/routes.py (se já não estiver).
3) Verifique que templates/base.html possui o bloco:
   {% block extra_js %}{% endblock %}
4) Reinicie a app e force reload (Ctrl+F5).