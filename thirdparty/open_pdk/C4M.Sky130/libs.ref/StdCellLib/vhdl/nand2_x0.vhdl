--  
--  Avertec Release v3.4p5 (64 bits on Linux 5.10.0-0.bpo.9-amd64)
--  [AVT_only] host: fsdev
--  [AVT_only] arch: x86_64
--  [AVT_only] path: /opt/tasyag-3.4p5/bin/avt_shell
--  argv: 
--  
--  User: verhaegs
--  Generation date Fri Dec 10 15:33:06 2021
--  
--  VHDL data flow description generated from `nand2_x0`
--  

library IEEE;
use IEEE.std_logic_1164.all;

-- Entity Declaration

ENTITY nand2_x0 IS
  PORT (
         nq : out   STD_LOGIC;
         i0 : in    STD_LOGIC;
         i1 : in    STD_LOGIC
  );
END nand2_x0;

-- Architecture Declaration

ARCHITECTURE RTL OF nand2_x0 IS

BEGIN


  nq <= (not (i1) or not (i0));

END;
