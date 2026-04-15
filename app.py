import os
import resend
import sqlite3
import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_apscheduler import APScheduler
import json

app = Flask(__name__)
CORS(app) 

# ==========================================
# CONFIGURAÇÃO DE BANCO DE DADOS (NUVEM vs LOCAL)
# ==========================================

db_url = os.getenv("DATABASE_URL", "sqlite:///gprocess.db")

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
scheduler = APScheduler()

resend.api_key = os.getenv("RESEND_API_KEY") 
EMAIL_REMETENTE_RESEND = "onboarding@resend.dev"
EMAIL_GESTOR = "alexandre.ardc@gmail.com"



# ==========================================
# 1. MODELOS DE DADOS
# ==========================================

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)
    perfil = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='Ativo')
    
    def to_dict(self):
        return {"id": self.id, "nome": self.nome, "email": self.email, "perfil": self.perfil, "status": self.status}

class Loja(db.Model):
    __tablename__ = 'lojas'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nome = db.Column(db.String(100), nullable=False)
    bandeira = db.Column(db.String(50), nullable=False)
    gd = db.Column(db.String(100), default='Não Definido')
    status = db.Column(db.String(20), default='Ativa')
    bk_number = db.Column(db.String(20))
    cnpj = db.Column(db.String(30))
    endereco = db.Column(db.String(255))

    def to_dict(self):
        return {
            "id": self.id, "nome": self.nome, "bandeira": self.bandeira, 
            "gd": self.gd, "status": self.status, "bk_number": self.bk_number,
            "cnpj": self.cnpj, "endereco": self.endereco
        }

class LogAuditoria(db.Model):
    __tablename__ = 'logs_auditoria'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    data_hora = db.Column(db.DateTime, default=datetime.datetime.now)
    usuario = db.Column(db.String(100))
    modulo = db.Column(db.String(50))
    acao = db.Column(db.String(255))
    
    def to_dict(self):
        return {
            "id": self.id,
            "data": self.data_hora.strftime("%d/%m/%Y %H:%M:%S"),
            "usuario": self.usuario,
            "modulo": self.modulo,
            "acao": self.acao
        }
class ChamadoDB(db.Model):
    __tablename__ = 'chamados_db'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    os = db.Column(db.String(50))
    loja = db.Column(db.String(100))
    tecnico = db.Column(db.String(100))
    gerente = db.Column(db.String(100))
    data_visita = db.Column(db.String(20))
    hora_entrada = db.Column(db.String(10))
    hora_saida = db.Column(db.String(10))
    descricao = db.Column(db.String(255))
    ocorrido = db.Column(db.Text)
    itens_json = db.Column(db.Text) # Guarda os equipamentos retirados/entregues
    status = db.Column(db.String(50), default='CONCLUÍDO')
    data_registro = db.Column(db.DateTime, default=datetime.datetime.now)

    def to_dict(self):
        return {
            "id": self.id, "os": self.os, "loja": self.loja, "tecnico": self.tecnico,
            "gerente": self.gerente, "data_visita": self.data_visita, 
            "descricao": self.descricao, "status": self.status
        }
    
def registrar_log(usuario_nome, modulo, acao):
    try:
        novo_log = LogAuditoria(usuario=usuario_nome, modulo=modulo, acao=acao)
        db.session.add(novo_log)
        db.session.commit()
    except Exception as e:
        print(f"❌ Erro ao gravar log: {e}")

# =================
# 2. ROBÔ DE SLA
# =================


def enviar_alerta_sla(chamado_id, tecnico, descricao):
    try:
        conteudo = f"""
        ALERTA GPROCESS
        
        O chamado #{chamado_id} do técnico {tecnico} ultrapassou o SLA de 4 dias.
        Descrição: {descricao}
        """
        
        r = resend.Emails.send({
            "from": f"GProcess <{EMAIL_REMETENTE_RESEND}>",
            "to": [EMAIL_GESTOR],
            "subject": f"🚨 URGENTE: SLA Vencido - Chamado #{chamado_id}",
            "text": conteudo
        })
        
        print(f"📧 E-mail de alerta enviado via Resend! ID: {r.get('id')}")
    except Exception as e:
        print(f"❌ Erro ao enviar via API Resend: {e}")


@scheduler.task('interval', id='verificar_sla', hours=1)
def robo_de_cobranca_sla():
    with app.app_context():
        hoje = datetime.datetime.now()
        pendentes = LogAuditoria.query.filter(
            LogAuditoria.modulo == "Chamados",
            LogAuditoria.acao.contains("Abertura OS #"),
            ~LogAuditoria.acao.contains("CONCLUÍDO")
        ).all()

        for p in pendentes:
            diff = hoje - p.data_hora
            if diff.days >= 3:
                enviar_alerta_sla(p.id, p.usuario, p.acao)

# ========================
# 3. ROTAS DE LOGIN E LOGS
# ========================
@app.route('/login', methods=['POST'])
def login():
    try:
        dados = request.json
        user = Usuario.query.filter_by(email=dados.get('email'), senha=dados.get('senha')).first()
        if user:
            if user.status != 'Ativo':
                return jsonify({"resultado": "erro", "mensagem": "Usuário bloqueado. Fale com o admin."}), 403
            registrar_log(user.nome, "Sistema", "Realizou login no sistema")
            return jsonify({"resultado": "sucesso", "id": user.id, "nome": user.nome, "perfil": user.perfil, "email": user.email}), 200
        return jsonify({"resultado": "erro", "mensagem": "E-mail ou senha incorretos"}), 401
    except Exception as e:
        return jsonify({"resultado": "erro", "mensagem": str(e)}), 500
    
@app.route('/logs', methods=['GET'])
def listar_logs():
    logs = LogAuditoria.query.order_by(LogAuditoria.id.desc()).limit(100).all()
    return jsonify([l.to_dict() for l in logs])
# ================================
# ROTA PARA DAR BAIXA NO CHAMADO
# ================================
@app.route('/logs/baixar/<int:id>', methods=['PUT', 'OPTIONS'])
def baixar_log(id):
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200

    try:
        chamado = ChamadoDB.query.get(id)
        if chamado:
            chamado.status = "CONCLUÍDO"
            novo_log = LogAuditoria(
                usuario=chamado.tecnico, 
                modulo="Chamados", 
                acao=f"Baixa da OS #{chamado.os} na loja {chamado.loja}" 
            )
            db.session.add(novo_log)
            db.session.commit()
            return jsonify({"status": "sucesso"}), 200


        log = LogAuditoria.query.get(id)
        if log:
            if "CONCLUÍDO" not in log.acao:
                log.acao = log.acao.replace(" - [CONCLUÍDO]", "") # Limpa se já tiver
                log.acao = log.acao + " - [CONCLUÍDO]" 
                db.session.commit()
            return jsonify({"status": "sucesso"}), 200
            
        return jsonify({"status": "erro", "message": "Registro não encontrado"}), 404

    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "erro", "message": str(e)}), 500

# Deletar chamado.
@app.route('/logs/deletar/<int:id>', methods=['DELETE', 'OPTIONS'])
def deletar_log(id):
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200

    try:
        chamado = ChamadoDB.query.get(id)
        if chamado:
            db.session.delete(chamado)
            db.session.commit()
            return jsonify({"status": "sucesso"}), 200

        log = LogAuditoria.query.get(id)
        if log:
            db.session.delete(log)
            db.session.commit()
            return jsonify({"status": "sucesso"}), 200
            
        return jsonify({"status": "erro", "message": "Registro não encontrado"}), 404

    except Exception as e:
        db.session.rollback()
        print(f"❌ Erro ao deletar: {e}")
        return jsonify({"status": "erro", "message": str(e)}), 500

@app.route('/chamados/abrir', methods=['POST'])
def abrir_chamado():
    dados = request.json
    try:
        acao_log = f"Abertura OS #{dados.get('chamado')} na loja {dados.get('loja')}"
        registrar_log(dados.get('tecnico', 'Sistema'), "Chamados", acao_log)
        
        db.session.commit()
        return jsonify({"status": "sucesso"}), 201
    except Exception as e:
        db.session.rollback()
        print(f"❌ Erro no Python: {e}")
        return jsonify({"status": "erro", "message": str(e)}), 500

@app.route('/testar-email', methods=['GET'])
def rota_teste_email():
    try:
        enviar_alerta_sla("999", "Alexandre (Teste)", "Teste disparado via navegador")
        return jsonify({"status": "sucesso", "mensagem": "Comando de envio enviado ao Resend!"}), 200
    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)}), 500

# =============================
# 4. ROTAS DE USUÁRIOS (ADMIN)
# =============================
@app.route('/usuarios', methods=['GET'])
def listar_usuarios():
    users = Usuario.query.all()
    return jsonify([u.to_dict() for u in users])

@app.route('/usuarios/<int:id>', methods=['GET'])
def obter_usuario(id):
    u = Usuario.query.get(id)
    return jsonify(u.to_dict()) if u else (jsonify({"erro": "Não encontrado"}), 404)

@app.route('/usuarios/cadastrar', methods=['POST'])
def cadastrar_usuario():
    d = request.json
    novo = Usuario(nome=d['nome'], email=d['email'], senha=d.get('senha', '123456789'), perfil=d['perfil'])
    db.session.add(novo)
    db.session.commit()
    return jsonify({"status": "sucesso"}), 201

@app.route('/usuarios/editar/<int:id>', methods=['PUT'])
def editar_usuario(id):
    u = Usuario.query.get(id)
    if u:
        d = request.json
        u.nome = d['nome']; u.email = d['email']; u.perfil = d['perfil']
        db.session.commit()
        return jsonify({"status": "sucesso"})
    return jsonify({"erro": "Não encontrado"}), 404

@app.route('/usuarios/status', methods=['PUT'])
def mudar_status_usuario():
    d = request.json
    u = Usuario.query.get(d['id'])
    if u:
        u.status = d['status']
        db.session.commit()
        return jsonify({"status": "sucesso"})
    return jsonify({"erro": "Não encontrado"}), 404

# =========================
# 5. ROTAS DE LOJAS (ADMIN)
# =========================
@app.route('/lojas', methods=['GET'])
def listar_lojas():
    lojas = Loja.query.all()
    return jsonify([l.to_dict() for l in lojas])

@app.route('/lojas/<int:id>', methods=['GET'])
def obter_loja(id):
    l = Loja.query.get(id)
    return jsonify(l.to_dict()) if l else (jsonify({"erro": "Não encontrada"}), 404)

@app.route('/lojas/cadastrar', methods=['POST'])
def cadastrar_loja():
    d = request.json
    nova = Loja(nome=d['nome'], bandeira=d['bandeira'], gd=d.get('gd', 'Não Definido'))
    db.session.add(nova)
    db.session.commit()
    return jsonify({"status": "sucesso"}), 201

@app.route('/lojas/editar/<int:id>', methods=['PUT'])
def editar_loja(id):
    l = Loja.query.get(id)
    if l:
        d = request.json
        l.nome = d['nome']; l.bandeira = d['bandeira']; l.gd = d['gd']
        l.bk_number = d.get('bk_number'); l.cnpj = d.get('cnpj'); l.endereco = d.get('endereco')
        db.session.commit()
        return jsonify({"status": "sucesso"})
    return jsonify({"erro": "Não encontrada"}), 404

# ===============
# INICIALIZAÇÃO
# ===============
def init_db():
    enviar_alerta_sla("TESTE", "Alexandre", "Teste de envio imediato") # ala teste 
    with app.app_context():
        db.create_all()
        # Injeção inicial de Usuários
        if not Usuario.query.first():
            tecs = ["Alexandre", "Carlos Cesar", "Everton", "Ludier", "Saulo", "Sandro", "Marcos", "Wesley"]
            for t in tecs:
                perfil = "Administrador" if t == "Alexandre" else "Técnico de TI"
                db.session.add(Usuario(nome=t, email=f"{t.lower().replace(' ','')}@gprocess.com.br", senha="123", perfil=perfil))
            db.session.commit()
            print("✅ Técnicos e Admin criados no Banco!")

        # Injeção inicial de Lojas
        if not Loja.query.first():
            lojas = ["BK10 LARGO TREZE", "BK11 POLO SHOPPING", "BK12 PRAÇA DA MOÇA", "BK13 SHOPPING LIGHT", "BK15 MOOCA", "BK16 JK IGUATEMI", "BK18 THE SQUARE", "BK20 SÃO BERNARDO DO CAMPO", "BK21 LIMEIRA", "BK22 ITU", "BK23 GOLDEN SQUARE", "BK24 BONSUCESSO", "BK27 JARAGUÁ", "BK28 TAUBATÉ", "BK30 ITAQUERA", "BK31 SHOPPING MAIA", "BK32 TAUBATÉ DRIVE", "BK34 LIBERDADE", "BK37 INTERNACIONAL", "BK40 BRAGANÇA PAULISTA", "BK44 25 DE MARÇO", "BK45 SÃO CAETANO DO SUL", "BK50 CONSELHEIRO", "BK52 SÃO MIGUEL DRIVE", "BK54 ANDORINHA", "BK56 JOÃO DIAS DRIVE", "BK57 ATIBAIA DRIVE", "BK62 DIADEMA DRIVE", "BK63 PEDROSO DE MORAIS", "BK66 ITAQUERA NOVA EXTENSÃO", "BK67 JOSÉ MARIA WHITAKER", "BK69 CERRO CORÁ", "BK70 AUGUSTA", "BK76 ANGÉLICA", "BK81 INTERNACIONAL 2", "BK91 TABOÃO DRIVE", "BK95 CENTRAL PARK", "BK96 PASEO ALTO DAS NAÇOES", "BK97 KIZAEMON", "BK98 BANDEIRANTES"]
            for l in lojas:
                db.session.add(Loja(nome=l, bandeira="Burger King Brasil"))
            db.session.commit()
            print("✅ 40 Lojas BK criadas no Banco!")

init_db()
scheduler.init_app(app)
scheduler.start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
