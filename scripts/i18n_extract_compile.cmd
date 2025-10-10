@echo off
REM Extrai mensagens
pybabel extract -F babel.cfg -o messages.pot .
IF ERRORLEVEL 1 EXIT /B 1

REM Inicia catálogos (roda só UMA vez por idioma novo; comente após criado)
IF NOT EXIST translations\en\LC_MESSAGES\messages.po pybabel init -i messages.pot -d translations -l en
IF NOT EXIST translations\es\LC_MESSAGES\messages.po pybabel init -i messages.pot -d translations -l es
REM Se quiser pt-BR explícito:
REM IF NOT EXIST translations\pt\LC_MESSAGES\messages.po pybabel init -i messages.pot -d translations -l pt

REM Atualiza catálogos (após editar HTML/Python com novos textos)
pybabel update -i messages.pot -d translations
IF ERRORLEVEL 1 EXIT /B 1

REM Compile MO
pybabel compile -d translations
IF ERRORLEVEL 1 EXIT /B 1

echo.
echo I18N OK!
