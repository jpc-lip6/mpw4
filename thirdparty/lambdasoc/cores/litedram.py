from abc import ABCMeta, abstractmethod
import csv
import jinja2
import os
import re
import textwrap

from nmigen import *
from nmigen import tracer
from nmigen.build.run import BuildPlan, BuildProducts
from nmigen.utils import log2_int

from thirdparty.nmigen_soc import wishbone
from thirdparty.nmigen_soc.memory import MemoryMap


__all__ = [
    "Config", "ECP5Config", "Artix7Config",
    "NativePort", "Core",
    "Builder",
]


class Config(metaclass=ABCMeta):
    _doc_template = """
    {description}

    Parameters
    ----------
    memtype : str
        DRAM type (e.g. `"DDR3"`).
    module_name : str
        DRAM module name.
    module_bytes : int
        Number of byte groups of the DRAM interface.
    module_ranks : int
        Number of ranks. A rank is a set of DRAM chips that are connected to the same CS pin.
    input_clk_freq : int
        Frequency of the input clock, which drives the internal PLL.
    user_clk_freq : int
        Frequency of the user clock, which is generated by the internal PLL.
    input_domain : str
        Input clock domain. Defaults to `"litedram_input"`.
    user_domain : str
        User clock domain. Defaults to `"litedram_user"`.
    user_data_width : int
        User port data width. Defaults to 128.
    cmd_buffer_depth : int
        Command buffer depth. Defaults to 16.
    csr_data_width : int
        CSR bus data width. Defaults to 32.
    {parameters}
    """

    __doc__ = _doc_template.format(
    description="""
    LiteDRAM base configuration.
    """.strip(),
    parameters="",
    )
    def __init__(self, *,
            memtype,
            module_name,
            module_bytes,
            module_ranks,
            input_clk_freq,
            user_clk_freq,
            input_domain     = "litedram_input",
            user_domain      = "litedram_user",
            user_data_width  = 128,
            cmd_buffer_depth = 16,
            csr_data_width   = 32):

        if memtype == "DDR2":
            rate = "1:2"
        elif memtype in {"DDR3", "DDR4"}:
            rate = "1:4"
        else:
            raise ValueError("Unsupported DRAM type, must be one of \"DDR2\", \"DDR3\" or "
                             "\"DDR4\", not {!r}"
                             .format(memtype))

        if not isinstance(module_name, str):
            raise ValueError("Module name must be a string, not {!r}"
                             .format(module_name))
        if not isinstance(module_bytes, int) or module_bytes <= 0:
            raise ValueError("Number of byte groups must be a positive integer, not {!r}"
                             .format(module_bytes))
        if not isinstance(module_ranks, int) or module_ranks <= 0:
            raise ValueError("Number of ranks must be a positive integer, not {!r}"
                             .format(module_ranks))
        if not isinstance(input_clk_freq, int) or input_clk_freq <= 0:
            raise ValueError("Input clock frequency must be a positive integer, not {!r}"
                             .format(input_clk_freq))
        if not isinstance(user_clk_freq, int) or user_clk_freq <= 0:
            raise ValueError("User clock frequency must be a positive integer, not {!r}"
                             .format(user_clk_freq))
        if not isinstance(input_domain, str):
            raise ValueError("Input domain name must be a string, not {!r}"
                             .format(input_domain))
        if not isinstance(user_domain, str):
            raise ValueError("User domain name must be a string, not {!r}"
                             .format(user_domain))
        if user_data_width not in {8, 16, 32, 64, 128}:
            raise ValueError("User port data width must be one of 8, 16, 32, 64 or 128, "
                             "not {!r}"
                             .format(user_data_width))
        if not isinstance(cmd_buffer_depth, int) or cmd_buffer_depth <= 0:
            raise ValueError("Command buffer depth must be a positive integer, not {!r}"
                             .format(cmd_buffer_depth))
        if csr_data_width not in {8, 16, 32, 64}:
            raise ValueError("CSR data width must be one of 8, 16, 32, or 64, not {!r}"
                             .format(csr_data_width))

        self.memtype          = memtype
        self._rate            = rate
        self.module_name      = module_name
        self.module_bytes     = module_bytes
        self.module_ranks     = module_ranks
        self.input_clk_freq   = input_clk_freq
        self.user_clk_freq    = user_clk_freq
        self.input_domain     = input_domain
        self.user_domain      = user_domain
        self.user_data_width  = user_data_width
        self.cmd_buffer_depth = cmd_buffer_depth
        self.csr_data_width   = csr_data_width

    @property
    @abstractmethod
    def phy_name(self):
        """LiteDRAM PHY name.
        """
        raise NotImplementedError

    def get_module(self):
        """Get DRAM module description.

        Return value
        ------------
        An instance of :class:`litedram.modules.SDRAMModule`, describing its geometry and timings.
        """
        import litedram.modules
        module_class = getattr(litedram.modules, self.module_name)
        module = module_class(
            clk_freq = self.user_clk_freq,
            rate     = self._rate,
        )
        assert module.memtype == self.memtype
        return module

    def request_pins(self, platform, name, number):
        """Request DRAM pins.

        This helper requests the DRAM pins with `dir="-"` and `xdr=0`, because LiteDRAM already
        provides its own I/O buffers.

        Arguments
        ---------
        platform : :class:`nmigen.build.Platform`
            Target platform.
        name : str
            DRAM resource name.
        number : int
            DRAM resource number.

        Return value
        ------------
        A :class:`Record` providing raw access to DRAM pins.
        """
        res = platform.lookup(name, number)
        return platform.request(
            name, number,
            dir={io.name: "-" for io in res.ios},
            xdr={io.name: 0   for io in res.ios},
        )


class ECP5Config(Config):
    phy_name = "ECP5DDRPHY"

    __doc__ = Config._doc_template.format(
    description = """
    LiteDRAM configuration for ECP5 FPGAs.
    """.strip(),
    parameters  = r"""
    init_clk_freq : int
        Frequency of the PHY initialization clock, which is generated by the internal PLL.
    """.strip(),
    )
    def __init__(self, *, init_clk_freq, **kwargs):
        super().__init__(**kwargs)

        if not isinstance(init_clk_freq, int) or init_clk_freq <= 0:
            raise ValueError("Init clock frequency must be a positive integer, not {!r}"
                             .format(init_clk_freq))
        self.init_clk_freq = init_clk_freq


class Artix7Config(Config):
    phy_name = "A7DDRPHY"

    __doc__ = Config._doc_template.format(
    description = """
    LiteDRAM configuration for Artix 7 FPGAs.
    """.strip(),
    parameters  = r"""
    speedgrade : str
        FPGA speed grade (e.g. "-1").
    cmd_latency : int
        Command additional latency.
    rtt_nom : int
        Nominal termination impedance.
    rtt_wr : int
        Write termination impedance.
    ron : int
        Output driver impedance.
    iodelay_clk_freq : int
        IODELAY reference clock frequency.
    """.strip(),
    )
    def __init__(self, *,
            speedgrade,
            cmd_latency,
            rtt_nom,
            rtt_wr,
            ron,
            iodelay_clk_freq,
            **kwargs):
        super().__init__(**kwargs)

        speedgrades = ("-1", "-2", "-2L", "-2G", "-3")
        if speedgrade not in speedgrades:
            raise ValueError("Speed grade must be one of \'{}\', not {!r}"
                             .format("\', \'".join(speedgrades), speedgrade))
        if not isinstance(cmd_latency, int) or cmd_latency < 0:
            raise ValueError("Command latency must be a non-negative integer, not {!r}"
                             .format(cmd_latency))
        if not isinstance(rtt_nom, int) or rtt_nom < 0:
            raise ValueError("Nominal termination impedance must be a non-negative integer, "
                             "not {!r}"
                             .format(rtt_nom))
        if not isinstance(rtt_wr, int) or rtt_wr < 0:
            raise ValueError("Write termination impedance must be a non-negative integer, "
                             "not {!r}"
                             .format(rtt_wr))
        if not isinstance(ron, int) or ron < 0:
            raise ValueError("Output driver impedance must be a non-negative integer, "
                             "not {!r}"
                             .format(ron))
        if not isinstance(iodelay_clk_freq, int) or iodelay_clk_freq <= 0:
            raise ValueError("IODELAY clock frequency must be a positive integer, not {!r}"
                             .format(iodelay_clk_freq))

        self.speedgrade       = speedgrade
        self.cmd_latency      = cmd_latency
        self.rtt_nom          = rtt_nom
        self.rtt_wr           = rtt_wr
        self.ron              = ron
        self.iodelay_clk_freq = iodelay_clk_freq


class NativePort(Record):
    """LiteDRAM native port interface.

    In the "Attributes" section, port directions are given from the point of view of user logic.

    Parameters
    ----------
    addr_width : int
        Port address width.
    data_width : int
        Port data width.

    Attributes
    ----------
    granularity : int
        Port granularity, i.e. its smallest transferable unit of data. LiteDRAM native ports have a
        granularity of 8 bits.
    cmd.valid : Signal(), in
        Command valid.
    cmd.ready : Signal(), out
        Command ready. Commands are accepted when `cmd.valid` and `cmd.ready` are both asserted.
    cmd.last : Signal(), in
        Command last. Indicates the last command of a burst.
    cmd.we : Signal(), in
        Command write enable. Indicates that this command is a write.
    cmd.addr : Signal(addr_width), in
        Command address.
    w.valid : Signal(), in
        Write valid.
    w.ready : Signal(), out
        Write ready. Write data is accepted when `w.valid` and `w.ready` are both asserted.
    w.data : Signal(data_width), in
        Write data.
    w.we : Signal(data_width // granularity), bitmask, in
        Write mask. Indicates which bytes in `w.data` are valid.
    r.valid : Signal(), out
        Read valid.
    r.ready : Signal(), in
        Read ready. Read data is consumed when `r.valid` and `r.ready` are both asserted.
    r.data : Signal(data_width), out
        Read data.
    """
    def __init__(self, *, addr_width, data_width, name=None, src_loc_at=0):
        if not isinstance(addr_width, int) or addr_width <= 0:
            raise ValueError("Address width must be a positive integer, not {!r}"
                             .format(addr_width))
        if not isinstance(data_width, int) or data_width <= 0 or data_width & data_width - 1:
            raise ValueError("Data width must be a positive power of two integer, not {!r}"
                             .format(data_width))

        self.addr_width  = addr_width
        self.data_width  = data_width
        self.granularity = 8
        self._map        = None

        super().__init__([
            ("cmd", [
                ("valid", 1),
                ("ready", 1),
                ("last",  1),
                ("we",    1),
                ("addr",  addr_width),
            ]),
            ("w", [
                ("valid", 1),
                ("ready", 1),
                ("data",  data_width),
                ("we",    data_width // self.granularity),
            ]),
            ("r", [
                ("valid", 1),
                ("ready", 1),
                ("data",  data_width),
            ]),
        ], name=name, src_loc_at=1 + src_loc_at)

    @property
    def memory_map(self):
        """Map of the native port.

        Return value
        ------------
        An instance of :class:`nmigen_soc.memory.MemoryMap`.

        Exceptions
        ----------
        Raises an :exn:`AttributeError` if the port does not have a memory map.
        """
        if self._map is None:
            raise AttributeError("Native port {!r} does not have a memory map"
                                 .format(self))
        return self._map

    @memory_map.setter
    def memory_map(self, memory_map):
        if not isinstance(memory_map, MemoryMap):
            raise TypeError("Memory map must be an instance of MemoryMap, not {!r}"
                            .format(memory_map))
        if memory_map.data_width != 8:
            raise ValueError("Memory map has data width {}, which is not the same as native port "
                             "granularity {}"
                             .format(memory_map.data_width, 8))
        granularity_bits = log2_int(self.data_width // 8)
        if memory_map.addr_width != max(1, self.addr_width + granularity_bits):
            raise ValueError("Memory map has address width {}, which is not the same as native "
                             "port address width {} ({} address bits + {} granularity bits)"
                             .format(memory_map.addr_width, self.addr_width + granularity_bits,
                                     self.addr_width, granularity_bits))
        memory_map.freeze()
        self._map = memory_map


class Core(Elaboratable):
    """An nMigen wrapper for a standalone LiteDRAM core.

    Parameters
    ----------
    config : :class:`Config`
        LiteDRAM configuration.
    pins : :class:`nmigen.lib.io.Pin`
        Optional. DRAM pins. See :class:`nmigen_boards.resources.DDR3Resource` for layout.
    name : str
        Optional. Name of the LiteDRAM core. If ``None`` (default) the name is inferred from the
        name of the variable this instance is assigned to.
    name_force: bool
        Force name. If ``True``, no exception will be raised in case of a name collision with a
        previous LiteDRAM instance. Defaults to ``False``.

    Attributes
    ----------
    name : str
        Name of the LiteDRAM core.
    size : int
        DRAM size, in bytes.
    user_port : :class:`NativePort`
        User port. Provides access to the DRAM storage.

    Exceptions
    ----------
    Raises a :exn:`ValueError` if ``name`` collides with the name given to a previous LiteDRAM
    instance and ``name_force`` is ``False``.
    """
    def __init__(self, config, *, pins=None, name=None, name_force=False, src_loc_at=0):
        if not isinstance(config, Config):
            raise TypeError("Config must be an instance of litedram.Config, "
                            "not {!r}"
                            .format(config))
        self.config = config

        if name is not None and not isinstance(name, str):
            raise TypeError("Name must be a string, not {!r}".format(name))
        self.name = name or tracer.get_var_name(depth=2 + src_loc_at)

        module = config.get_module()
        size   = config.module_bytes \
               * 2**( module.geom_settings.bankbits
                    + module.geom_settings.rowbits
                    + module.geom_settings.colbits)

        self.size = size

        user_addr_width = module.geom_settings.rowbits \
                        + module.geom_settings.colbits \
                        + log2_int(module.nbanks) \
                        + max(log2_int(config.module_ranks), 1)

        self.user_port = NativePort(
            addr_width = user_addr_width - log2_int(config.user_data_width // 8),
            data_width = config.user_data_width,
        )
        user_map = MemoryMap(addr_width=user_addr_width, data_width=8)
        user_map.add_resource("user_port_0", size=size)
        self.user_port.memory_map = user_map

        self._ctrl_bus = None
        self._pins     = pins

    @property
    def ctrl_bus(self):
        """Control bus interface.

        *Please note that accesses to the CSRs exposed by this interface are not atomic.*

        The memory map of this interface is populated by reading the ``{{self.name}}_csr.csv``
        file from the build products.

        Return value
        ------------
        An instance of :class:`nmigen_soc.wishbone.Interface`.

        Exceptions
        ----------
        Raises an :exn:`AttributeError` if this getter is called before LiteDRAM is built (i.e.
        before :meth:`Core.build` is called with `do_build=True`).
        """
        if self._ctrl_bus is None:
            raise AttributeError("Control bus memory map has not been populated. "
                                 "Core.build(do_build=True) must be called before accessing "
                                 "Core.ctrl_bus")
        return self._ctrl_bus

    def _populate_ctrl_map(self, build_products):
        if not isinstance(build_products, BuildProducts):
            raise TypeError("Build products must be an instance of BuildProducts, not {!r}"
                            .format(build_products))

        # LiteDRAM's Wishbone to CSR bridge has a granularity of 8 bits.
        ctrl_map = MemoryMap(addr_width=1, data_width=8)

        csr_csv = build_products.get(f"{self.name}_csr.csv", mode="t")
        for row in csv.reader(csr_csv.split("\n"), delimiter=","):
            if not row or row[0][0] == "#": continue
            res_type, res_name, addr, size, attrs = row
            if res_type == "csr_register":
                ctrl_map.add_resource(
                    res_name,
                    addr   = int(addr, 16),
                    size   = int(size, 10) * self.config.csr_data_width // ctrl_map.data_width,
                    extend = True,
                )

        self._ctrl_bus = wishbone.Interface(
            addr_width  = ctrl_map.addr_width
                        - log2_int(self.config.csr_data_width // ctrl_map.data_width),
            data_width  = self.config.csr_data_width,
            granularity = ctrl_map.data_width,
        )
        self._ctrl_bus.memory_map = ctrl_map

    def build(self, builder, *, do_build=True, build_dir="build/litedram", sim=False,
            name_force=False):
        """Build the LiteDRAM core.

        Arguments
        ---------
        builder: :class:`litedram.Builder`
            Builder instance.
        do_build : bool
            Execute the build locally. Defaults to ``True``.
        build_dir : str
            Root build directory. Defaults to ``"build/litedram"``.
        sim : bool
            Do the build in simulation mode (i.e. by replacing the PHY with a model). Defaults to
            ``False``.
        name_force : bool
            Ignore builder name conflicts. Defaults to ``False``.

        Return value
        ------------
        An instance of :class:`nmigen.build.run.LocalBuildProducts` if ``do_build`` is ``True``.
        Otherwise, an instance of :class:``nmigen.build.run.BuildPlan``.
        """
        if not isinstance(builder, Builder):
            raise TypeError("Builder must be an instance of litedram.Builder, not {!r}"
                            .format(builder))

        plan = builder.prepare(self, sim=sim, name_force=name_force)
        if not do_build:
            return plan

        products = plan.execute_local(build_dir)
        self._populate_ctrl_map(products)
        return products

    def elaborate(self, platform):
        core_kwargs = {
            "i_clk"      : ClockSignal(self.config.input_domain),
            "i_rst"      : ResetSignal(self.config.input_domain),
            "o_user_clk" : ClockSignal(self.config.user_domain),
            "o_user_rst" : ResetSignal(self.config.user_domain),

            "i_wb_ctrl_adr"   : self.ctrl_bus.adr,
            "i_wb_ctrl_dat_w" : self.ctrl_bus.dat_w,
            "o_wb_ctrl_dat_r" : self.ctrl_bus.dat_r,
            "i_wb_ctrl_sel"   : self.ctrl_bus.sel,
            "i_wb_ctrl_cyc"   : self.ctrl_bus.cyc,
            "i_wb_ctrl_stb"   : self.ctrl_bus.stb,
            "o_wb_ctrl_ack"   : self.ctrl_bus.ack,
            "i_wb_ctrl_we"    : self.ctrl_bus.we,

            "i_user_port_0_cmd_valid"   : self.user_port.cmd.valid,
            "o_user_port_0_cmd_ready"   : self.user_port.cmd.ready,
            "i_user_port_0_cmd_we"      : self.user_port.cmd.we,
            "i_user_port_0_cmd_addr"    : self.user_port.cmd.addr,
            "i_user_port_0_wdata_valid" : self.user_port.w.valid,
            "o_user_port_0_wdata_ready" : self.user_port.w.ready,
            "i_user_port_0_wdata_we"    : self.user_port.w.we,
            "i_user_port_0_wdata_data"  : self.user_port.w.data,
            "o_user_port_0_rdata_valid" : self.user_port.r.valid,
            "i_user_port_0_rdata_ready" : self.user_port.r.ready,
            "o_user_port_0_rdata_data"  : self.user_port.r.data,
        }

        if self._pins is not None:
            core_kwargs.update({
                "o_ddram_a"     : self._pins.a,
                "o_ddram_ba"    : self._pins.ba,
                "o_ddram_ras_n" : self._pins.ras,
                "o_ddram_cas_n" : self._pins.cas,
                "o_ddram_we_n"  : self._pins.we,
                "o_ddram_dm"    : self._pins.dm,
                "o_ddram_clk_p" : self._pins.clk.p,
                "o_ddram_cke"   : self._pins.clk_en,
                "o_ddram_odt"   : self._pins.odt,
            })

            if hasattr(self._pins, "cs"):
                core_kwargs.update({
                    "o_ddram_cs_n" : self._pins.cs,
                })

            if hasattr(self._pins, "rst"):
                core_kwargs.update({
                    "o_ddram_reset_n" : self._pins.rst,
                })

            if isinstance(self.config, ECP5Config):
                core_kwargs.update({
                    "i_ddram_dq"    : self._pins.dq,
                    "i_ddram_dqs_p" : self._pins.dqs.p,
                })
            elif isinstance(self.config, Artix7Config):
                core_kwargs.update({
                    "io_ddram_dq"    : self._pins.dq,
                    "io_ddram_dqs_p" : self._pins.dqs.p,
                    "io_ddram_dqs_n" : self._pins.dqs.n,
                    "o_ddram_clk_n"  : self._pins.clk.n,
                })
            else:
                assert False

        return Instance(f"{self.name}", **core_kwargs)


class Builder:
    file_templates = {
        "build_{{top.name}}.sh": r"""
            # {{autogenerated}}
            set -e
            {{emit_commands()}}
        """,
        "{{top.name}}_config.yml": r"""
            # {{autogenerated}}
            {
                # General ------------------------------------------------------------------
                "cpu":              "None",
                {% if top.config.phy_name == "A7DDRPHY" %}
                "speedgrade":       {{top.config.speedgrade}},
                {% endif %}
                "memtype":          "{{top.config.memtype}}",

                # PHY ----------------------------------------------------------------------
                {% if top.config.phy_name == "A7DDRPHY" %}
                "cmd_latency":      {{top.config.cmd_latency}},
                {% endif %}
                "sdram_module":     "{{top.config.module_name}}",
                "sdram_module_nb":  {{top.config.module_bytes}},
                "sdram_rank_nb":    {{top.config.module_ranks}},
                "sdram_phy":        "{{top.config.phy_name}}",

                # Electrical ---------------------------------------------------------------
                {% if top.config.phy_name == "A7DDRPHY" %}
                "rtt_nom":          "{{top.config.rtt_nom}}ohm",
                "rtt_wr":           "{{top.config.rtt_wr}}ohm",
                "ron":              "{{top.config.ron}}ohm",
                {% endif %}

                # Frequency ----------------------------------------------------------------
                "input_clk_freq":   {{top.config.input_clk_freq}},
                "sys_clk_freq":     {{top.config.user_clk_freq}},
                {% if top.config.phy_name == "ECP5DDRPHY" %}
                "init_clk_freq":    {{top.config.init_clk_freq}},
                {% elif top.config.phy_name == "A7DDRPHY" %}
                "iodelay_clk_freq": {{top.config.iodelay_clk_freq}},
                {% endif %}

                # Core ---------------------------------------------------------------------
                "cmd_buffer_depth": {{top.config.cmd_buffer_depth}},
                "csr_data_width":   {{top.config.csr_data_width}},

                # User Ports ---------------------------------------------------------------
                "user_ports": {
                    "0": {
                        "type":       "native",
                        "data_width": {{top.config.user_data_width}},
                    },
                },
            }
        """,
    }
    command_templates = [
        r"""
            python -m litedram.gen
                --name {{top.name}}
                --output-dir {{top.name}}
                --gateware-dir {{top.name}}
                --csr-csv {{top.name}}_csr.csv
                {% if sim %}
                --sim
                {% endif %}
                {{top.name}}_config.yml
        """,
    ]

    """LiteDRAM builder.

    Build products
    --------------

    Any platform:
        * ``{{top.name}}_csr.csv`` : CSR listing.
        * ``{{top.name}}/build_{{top.name}}.sh``: LiteDRAM build script.
        * ``{{top.name}}/{{top.name}}.v`` : LiteDRAM core.
        * ``{{top.name}}/software/include/generated/csr.h`` : CSR accessors.
        * ``{{top.name}}/software/include/generated/git.h`` : Git version.
        * ``{{top.name}}/software/include/generated/mem.h`` : Memory regions.
        * ``{{top.name}}/software/include/generated/sdram_phy.h`` : SDRAM initialization sequence.
        * ``{{top.name}}/software/include/generated/soc.h`` : SoC constants.

    Lattice ECP5 platform:
        * ``{{top.name}}/{{top.name}}.lpf`` : Constraints file.
        * ``{{top.name}}/{{top.name}}.ys`` : Yosys script.

    Xilinx Artix7 platform:
        * ``{{top.name}}/{{top.name}}.xdc`` : Constraints file
        * ``{{top.name}}/{{top.name}}.tcl`` : Vivado script.

    Name conflict avoidance
    -----------------------

    Every time :meth:`litedram.Builder.prepare` is called, the name of the :class:`litedram.Core`
    instance is added to ``namespace`. This allows the detection of name conflicts, which are
    problematic for the following reasons:
        * if two build plans are executed locally within the same root directory, the latter could
          overwrite the products of the former.
        * the LiteDRAM instance name becomes the name of its top-level Verilog module; importing
          two modules with the same name will cause a toolchain error.

    Attributes
    ----------
    namespace : set(str)
        Builder namespace.
    """
    def __init__(self):
        self.namespace = set()

    def prepare(self, core, *, sim=False, name_force=False):
        """Prepare a build plan.

        Arguments
        ---------
        core : :class:`litedram.Core`
            The LiteDRAM instance to be built.
        sim : bool
            Do the build in simulation mode (i.e. by replacing the PHY with a model).
        name_force : bool
            Force name. If ``True``, no exception will be raised in case of a name conflict with a
            previous LiteDRAM instance.

        Return value
        ------------
        A :class:`nmigen.build.run.BuildPlan` for this LiteDRAM instance.

        Exceptions
        ----------
        Raises a :exn:`ValueError` if ``core.name`` conflicts with a previous build plan and
        ``name_force`` is ``False``.
        """
        if not isinstance(core, Core):
            raise TypeError("LiteDRAM core must be an instance of litedram.Core, not {!r}"
                            .format(core))

        if core.name in self.namespace and not name_force:
            raise ValueError(
                "LiteDRAM core name '{}' has already been used for a previous build. Building "
                "this instance may overwrite previous build products. Passing `name_force=True` "
                "will disable this check".format(core.name)
            )
        self.namespace.add(core.name)

        autogenerated = f"Automatically generated by LambdaSoC. Do not edit."

        def emit_commands():
            commands = []
            for index, command_tpl in enumerate(self.command_templates):
                command = render(command_tpl, origin="<command#{}>".format(index + 1))
                command = re.sub(r"\s+", " ", command)
                commands.append(command)
            return "\n".join(commands)

        def render(source, origin):
            try:
                source = textwrap.dedent(source).strip()
                compiled = jinja2.Template(source, trim_blocks=True, lstrip_blocks=True)
            except jinja2.TemplateSyntaxError as e:
                e.args = ("{} (at {}:{})".format(e.message, origin, e.lineno),)
                raise
            return compiled.render({
                "autogenerated": autogenerated,
                "emit_commands": emit_commands,
                "sim": sim,
                "top": core,
            })

        plan = BuildPlan(script=f"build_{core.name}")
        for filename_tpl, content_tpl in self.file_templates.items():
            plan.add_file(render(filename_tpl, origin=filename_tpl),
                          render(content_tpl,  origin=content_tpl))
        return plan